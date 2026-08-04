import argparse
import asyncio
import logging
import json
import os
from dotenv import load_dotenv
import csv
import time
from workload import WorkloadGenerator
from chaos import HCPTerraformController, SSHMeshController
from evaluator import ResilienceEvaluator

# Load environment variables from the .env file automatically
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger("main")

async def monitor_rto(generator: WorkloadGenerator, baseline_tps: float) -> float:
    """Monitors metric stream and calculates RTO."""
    rto_start = None
    target_tps = baseline_tps * 0.8
    logger.info(f"Target TPS for recovery: {target_tps:.2f}")

    async for metrics in generator.get_metrics_stream():
        logger.info(f"Metrics | TPS: {metrics['tps']:.1f} | p50: {metrics['p50']:.4f}s | p99: {metrics['p99']:.4f}s | Err: {metrics['error_rate']:.1f}%")

        # Detect impact (TPS drops significantly or errors spike)
        if rto_start is None and (metrics['tps'] < target_tps or metrics['error_rate'] > 0):
            rto_start = asyncio.get_event_loop().time()
            logger.warning("FAULT IMPACT DETECTED. Starting RTO clock...")

        # Detect recovery
        if rto_start is not None and metrics['tps'] >= target_tps and metrics['error_rate'] == 0:
            rto = asyncio.get_event_loop().time() - rto_start
            logger.info(f"CLUSTER RECOVERED! RTO: {rto:.2f} seconds.")
            return rto

async def run_tf_chaos(args):
    # 1. Initialize workload
    generator = WorkloadGenerator(args.db_uri, concurrency=30)
    await generator.start()

    # 2. Establish Baseline
    logger.info("Establishing baseline performance for 15 seconds...")
    await asyncio.sleep(15)
    baseline_tps = 0
    # Process initial queue to get baseline
    while not generator.metrics_queue.empty():
        m = await generator.metrics_queue.get()
        baseline_tps += 1
    baseline_tps = baseline_tps / 15.0
    logger.info(f"Baseline established at {baseline_tps:.2f} TPS")

    # 3. Inject Chaos
    rto_task = asyncio.create_task(monitor_rto(generator, baseline_tps))

    tf_ctrl = HCPTerraformController(args.tf_org, args.tf_workspace, args.tf_token)
    logger.warning("INJECTING CHAOS: Modifying HCP Terraform State...")

    var_data = tf_ctrl.get_variable(args.target_config)

    # Parse existing HCL/JSON map
    config = json.loads(var_data["attributes"]["value"])
    config["nodes"][args.target_node]["enabled"] = False

    tf_ctrl.update_variable(var_data["id"], config)
    run_id = tf_ctrl.trigger_run(f"Chaos Engineering: Disabling {args.target_node}")
    tf_ctrl.wait_for_run(run_id)

    # 4. Wait for RTO calculation
    rto = await rto_task

    # 5. Evaluate RPO & Topology
    evaluator = ResilienceEvaluator(args.db_uri)
    rpo = await evaluator.calculate_rpo(generator.client_id, generator.max_acked_seq)
    leases = await evaluator.get_leaseholders()
    logger.info(f"Post-Outage Topology (Leaseholders per Node ID): {leases}")

    # 6. Cleanup Workload
    await generator.stop()
    logger.info(f"EXPERIMENT COMPLETE. RTO: {rto:.2f}s | Data Loss (RPO): {rpo} records.")

async def run_ssh_chaos(args):
    # Similar lifecycle, but trigger network partition via SSH
    generator = WorkloadGenerator(args.db_uri, concurrency=30)
    await generator.start()
    await asyncio.sleep(15) # Baseline

    rto_task = asyncio.create_task(monitor_rto(generator, 50.0)) # Hardcoded baseline for brevity

    logger.warning(f"INJECTING CHAOS: Partitioning {args.target_ip} via SSH...")
    await SSHMeshController.toggle_tailscale(args.target_ip, args.ssh_key, args.ssh_user, enable=False)

    await asyncio.sleep(30) # Let cluster react

    logger.info("Restoring network partition...")
    await SSHMeshController.toggle_tailscale(args.target_ip, args.ssh_key, args.ssh_user, enable=True)

    await rto_task

    evaluator = ResilienceEvaluator(args.db_uri)
    await evaluator.calculate_rpo(generator.client_id, generator.max_acked_seq)
    await generator.stop()

async def run_baseline(args):
    generator = WorkloadGenerator(args.db_uri, concurrency=args.concurrency)
    await generator.start()
    
    logger.info(f"Running baseline simulation for {args.duration} seconds...")
    logger.info(f"Saving telemetry to {args.output}")
    
    start_time = time.time()
    
    with open(args.output, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "tps", "p50_latency_s", "p99_latency_s", "error_rate_pct"])
        
        # Run until duration expires
        while (time.time() - start_time) < args.duration:
            async for metrics in generator.get_metrics_stream(window_seconds=1):
                writer.writerow([
                    time.time(),
                    metrics["tps"],
                    metrics["p50"],
                    metrics["p99"],
                    metrics["error_rate"]
                ])
                # Print to console as well
                logger.info(f"TPS: {metrics['tps']:.1f} | p99: {metrics['p99']:.4f}s | Err: {metrics['error_rate']:.1f}%")
                
                # Break the inner generator loop to re-evaluate the while condition
                break 

    await generator.stop()
    logger.info("Baseline simulation complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Cloud DB Chaos Executor")
    
    # Base arguments
    default_db_uri = os.getenv("DB_URI")
    parser.add_argument("--db-uri", default=default_db_uri, required=not default_db_uri, help="Postgres multi-host URI")
    
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Terraform Outage Subcommand
    tf_parser = subparsers.add_parser("tf-outage")
    
    default_org = os.getenv("HCP_ORG")
    tf_parser.add_argument("--tf-org", default=default_org, required=not default_org)
    
    default_ws = os.getenv("HCP_WORKSPACE")
    tf_parser.add_argument("--tf-workspace", default=default_ws, required=not default_ws)
    
    default_token = os.getenv("HCP_TOKEN")
    tf_parser.add_argument("--tf-token", default=default_token, required=not default_token, help="HCP API Token")
    
    tf_parser.add_argument("--target-config", required=True, help="e.g., aws_config")
    tf_parser.add_argument("--target-node", required=True, help="e.g., node1")

    # 2. Network Partition Subcommand
    net_parser = subparsers.add_parser("net-partition")
    net_parser.add_argument("--target-ip", required=True)
    net_parser.add_argument("--ssh-user", default="ubuntu")
    net_parser.add_argument("--ssh-key", required=True)

    # 3. Baseline Subcommand
    base_parser = subparsers.add_parser("baseline", help="Run workload and collect baseline metrics")
    base_parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    base_parser.add_argument("--concurrency", type=int, default=30, help="Number of concurrent workers")
    base_parser.add_argument("--output", default="baseline_metrics.csv", help="CSV output file")

    args = parser.parse_args()
    
    if args.command == "tf-outage":
        asyncio.run(run_tf_chaos(args))
    elif args.command == "net-partition":
        asyncio.run(run_ssh_chaos(args))
    elif args.command == "baseline":
        asyncio.run(run_baseline(args))

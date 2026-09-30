window.HYDRA_DOCS = {
 "cli": {
  "prog": "crdblab",
  "commands": [
   {
    "path": [],
    "help": "",
    "description": "Multi-cloud Database benchmarking and chaos testbed harness",
    "args": [
     {
      "flags": [
       "--engine"
      ],
      "positional": false,
      "help": "database engine deployed on the testbed, recorded in the run manifest and in every figure's filename (default: cockroachdb). Must precede the subcommand.",
      "default": "cockroachdb",
      "choices": [
       "cockroachdb",
       "postgresql"
      ],
      "required": false,
      "switch": false
     }
    ],
    "leaf": false
   },
   {
    "path": [
     "capture"
    ],
    "help": "capture raw generator output and report its column layout",
    "description": "",
    "args": [
     {
      "flags": [
       "--node"
      ],
      "positional": false,
      "help": "cluster node on which to run the generator, to pin the column layout the deployed CockroachDB version emits; defaults to the gateway (crdblab.topology). A measured sweep instead runs the generator from the dedicated client node, but the output format this captures does not depend on which node ran it, only on the CockroachDB version.",
      "default": "gcp-1",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--generator"
      ],
      "positional": false,
      "help": "",
      "default": "ycsb",
      "choices": [
       "ycsb",
       "kv"
      ],
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--concurrency"
      ],
      "positional": false,
      "help": "",
      "default": 10,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--duration"
      ],
      "positional": false,
      "help": "",
      "default": 15,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--workload"
      ],
      "positional": false,
      "help": "ycsb workload type A-F or CUSTOM (default: CUSTOM, with the 80/20 read/update mix below)",
      "default": "CUSTOM",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--read-freq"
      ],
      "positional": false,
      "help": "",
      "default": 0.8,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--update-freq"
      ],
      "positional": false,
      "help": "",
      "default": 0.2,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--seed"
      ],
      "positional": false,
      "help": "generator key seed; MUST match the seed the table was loaded with, or every lookup silently matches nothing",
      "default": 42,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--insert-count"
      ],
      "positional": false,
      "help": "size of the loaded keyspace; must match the value used at load time",
      "default": 125000,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--request-distribution"
      ],
      "positional": false,
      "help": "ycsb key distribution; uniform matches kv's scattered keys, whereas the CUSTOM default of zipfian would concentrate on a hot subset",
      "default": "uniform",
      "choices": [
       "uniform",
       "zipfian",
       "latest"
      ],
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--read-percent"
      ],
      "positional": false,
      "help": "",
      "default": 80,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--cycle-length"
      ],
      "positional": false,
      "help": "kv only; kv reads cannot reach pre-loaded rows regardless of this value",
      "default": 1000000,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--pty"
      ],
      "positional": false,
      "help": "allocate a pseudo-terminal",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     },
     {
      "flags": [
       "--output"
      ],
      "positional": false,
      "help": "",
      "default": "tests/fixtures/workload/captured.txt",
      "choices": null,
      "required": false,
      "switch": false
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "net"
    ],
    "help": "Phase I: network substrate characterisation",
    "description": "",
    "args": [],
    "leaf": false
   },
   {
    "path": [
     "net",
     "probe"
    ],
    "help": "measure the all-pairs RTT matrix and derive the quorum floor",
    "description": "",
    "args": [
     {
      "flags": [
       "--profile"
      ],
      "positional": false,
      "help": "",
      "default": "thesis",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--database"
      ],
      "positional": false,
      "help": "database whose leaseholder placement to check; must be the one the workload targets, since cluster-wide counts include system ranges",
      "default": "ycsb",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--no-checks"
      ],
      "positional": false,
      "help": "record the matrix without asserting clock offset and leaseholder placement",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "bench"
    ],
    "help": "Phase II: steady-state throughput and latency",
    "description": "",
    "args": [
     {
      "flags": [
       "--profile"
      ],
      "positional": false,
      "help": "",
      "default": "thesis",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--database"
      ],
      "positional": false,
      "help": "",
      "default": "ycsb",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--network-run"
      ],
      "positional": false,
      "help": "network.csv from a Phase I run, supplying the quorum floor; defaults to the most recent one in the runs directory",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--no-checks"
      ],
      "positional": false,
      "help": "skip pre-flight and the per-tier row-match probe. Produces a run that must not be used for figures; for harness debugging only.",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "probe"
    ],
    "help": "high-frequency availability probe, standalone (no workload generator)",
    "description": "",
    "args": [],
    "leaf": false
   },
   {
    "path": [
     "probe",
     "rto"
    ],
    "help": "write canary rows continuously and record when the database stopped and resumed serving them",
    "description": "",
    "args": [
     {
      "flags": [
       "--duration"
      ],
      "positional": false,
      "help": "seconds to probe for (default: 60)",
      "default": 60,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--profile"
      ],
      "positional": false,
      "help": "",
      "default": "thesis",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--database"
      ],
      "positional": false,
      "help": "database holding the canary table; deliberately not the workload's",
      "default": "bench",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--workers"
      ],
      "positional": false,
      "help": "concurrent in-flight canary writes; overrides the profile. This is the resolution dial: the gap between observations is roughly the write cost over this number",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--interval"
      ],
      "positional": false,
      "help": "dispatch cadence in seconds; overrides the profile. Ticks that find every worker busy are dropped and counted, so this is an upper bound on the sampling rate rather than the rate itself",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--keep-table"
      ],
      "positional": false,
      "help": "do not drop and recreate the canary table first. For probing a cluster you would rather not issue DDL against",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "chaos"
    ],
    "help": "Phases III-IV: fault injection, RTO and RPO",
    "description": "",
    "args": [],
    "leaf": false
   },
   {
    "path": [
     "chaos",
     "run"
    ],
    "help": "inject a fault into a steady-state run",
    "description": "",
    "args": [
     {
      "flags": [
       "--mode"
      ],
      "positional": false,
      "help": "dead: kill the process. recover: sever and restore the overlay network.",
      "default": null,
      "choices": [
       "dead",
       "recover"
      ],
      "required": true,
      "switch": false
     },
     {
      "flags": [
       "--profile"
      ],
      "positional": false,
      "help": "",
      "default": "thesis",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--database"
      ],
      "positional": false,
      "help": "",
      "default": "ycsb",
      "choices": null,
      "required": false,
      "switch": false
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "analyze"
    ],
    "help": "steady-state, engine-comparison and resilience analysis",
    "description": "",
    "args": [],
    "leaf": false
   },
   {
    "path": [
     "analyze",
     "steady-state"
    ],
    "help": "one run's throughput and latency by tier",
    "description": "",
    "args": [
     {
      "flags": [
       "run"
      ],
      "positional": true,
      "help": "run id or directory",
      "default": null,
      "choices": null,
      "required": true,
      "switch": false
     },
     {
      "flags": [
       "--json"
      ],
      "positional": false,
      "help": "",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "analyze",
     "engine-comparison"
    ],
    "help": "replication cost: CockroachDB against PostgreSQL+Patroni on the same five-node topology, at matched throughput",
    "description": "",
    "args": [
     {
      "flags": [
       "--crdb"
      ],
      "positional": false,
      "help": "CockroachDB run id or directory",
      "default": null,
      "choices": null,
      "required": true,
      "switch": false
     },
     {
      "flags": [
       "--pg"
      ],
      "positional": false,
      "help": "PostgreSQL run id or directory",
      "default": null,
      "choices": null,
      "required": true,
      "switch": false
     },
     {
      "flags": [
       "--op"
      ],
      "positional": false,
      "help": "operation type to compare; writes are the path replication affects",
      "default": "update",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--accept-hardware-difference"
      ],
      "positional": false,
      "help": "proceed even though the two runs were measured on different CPU models or memory sizes. Use only when that difference is a stated limitation of the study rather than a mistake; it is recorded as a warning in the output. Latency ratios on a network-bound path are least affected by it, absolute throughput most. Both engines run on the same five-node topology, so this should not normally be needed.",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     },
     {
      "flags": [
       "--json"
      ],
      "positional": false,
      "help": "",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "analyze",
     "resilience"
    ],
    "help": "Phases III-IV: RTO and RPO with their limits",
    "description": "",
    "args": [
     {
      "flags": [
       "run"
      ],
      "positional": true,
      "help": "chaos run id or directory",
      "default": null,
      "choices": null,
      "required": true,
      "switch": false
     },
     {
      "flags": [
       "--network-run"
      ],
      "positional": false,
      "help": "network.csv from a Phase I run, supplying the quorum geometry that explains an undefined performance RTO; defaults to the most recent",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--json"
      ],
      "positional": false,
      "help": "",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "report"
    ],
    "help": "render dissertation figures from validated runs",
    "description": "",
    "args": [],
    "leaf": false
   },
   {
    "path": [
     "report",
     "figures"
    ],
    "help": "render every figure whose inputs exist, as a PNG and an SVG each",
    "description": "",
    "args": [
     {
      "flags": [
       "--out"
      ],
      "positional": false,
      "help": "output directory. Filenames carry the engine, profile and run id behind each figure, so renders of different runs coexist here rather than overwriting each other (default: figures)",
      "default": "figures",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--network"
      ],
      "positional": false,
      "help": "Phase I run id (default: most recent)",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--cluster"
      ],
      "positional": false,
      "help": "benchmark cluster run id (default: most recent)",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--chaos"
      ],
      "positional": false,
      "help": "Phase III/IV run id; repeatable (default: the most recent run of each fault class, one figure per class)",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "validate"
    ],
    "help": "check a run for internal consistency",
    "description": "",
    "args": [
     {
      "flags": [
       "run"
      ],
      "positional": true,
      "help": "run directory or metrics.csv path",
      "default": null,
      "choices": null,
      "required": true,
      "switch": false
     },
     {
      "flags": [
       "--tps-ceiling"
      ],
      "positional": false,
      "help": "",
      "default": 20000.0,
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--json"
      ],
      "positional": false,
      "help": "",
      "default": null,
      "choices": null,
      "required": false,
      "switch": true
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "insights"
    ],
    "help": "draw the 31-chart catalogue, report and dashboard from the runs on disk",
    "description": "",
    "args": [
     {
      "flags": [
       "--out"
      ],
      "positional": false,
      "help": "parent directory; each render goes into its own <stamp>_<profile|all>/ beneath it (default: insights)",
      "default": "insights",
      "choices": null,
      "required": false,
      "switch": false
     },
     {
      "flags": [
       "--profile"
      ],
      "positional": false,
      "help": "only consider runs recorded under this profile (default: every profile, newest run of each kind per engine)",
      "default": null,
      "choices": null,
      "required": false,
      "switch": false
     }
    ],
    "leaf": true
   },
   {
    "path": [
     "profile"
    ],
    "help": "print a resolved experiment profile",
    "description": "",
    "args": [
     {
      "flags": [
       "name"
      ],
      "positional": true,
      "help": "",
      "default": "thesis",
      "choices": null,
      "required": false,
      "switch": false
     }
    ],
    "leaf": true
   }
  ]
 },
 "profiles": {
  "sections": {
   "workload": [
    {
     "name": "generator",
     "default": "'ycsb'",
     "doc": ""
    },
    {
     "name": "ycsb_workload",
     "default": "'CUSTOM'",
     "doc": "ycsb mix: CUSTOM with an explicit 80/20 read/update split, uniform keys."
    },
    {
     "name": "read_freq",
     "default": "0.8",
     "doc": ""
    },
    {
     "name": "update_freq",
     "default": "0.2",
     "doc": ""
    },
    {
     "name": "request_distribution",
     "default": "'uniform'",
     "doc": ""
    },
    {
     "name": "read_percent",
     "default": "80",
     "doc": "kv only."
    },
    {
     "name": "duration_s",
     "default": "60",
     "doc": ""
    },
    {
     "name": "warmup_s",
     "default": "5",
     "doc": ""
    },
    {
     "name": "display_every_s",
     "default": "1",
     "doc": ""
    },
    {
     "name": "concurrencies",
     "default": "(10, 50, 100, 200)",
     "doc": ""
    },
    {
     "name": "repetitions",
     "default": "3",
     "doc": ""
    },
    {
     "name": "randomise_tier_order",
     "default": "True",
     "doc": ""
    },
    {
     "name": "cooldown_s",
     "default": "15",
     "doc": ""
    },
    {
     "name": "seed",
     "default": "42",
     "doc": ""
    },
    {
     "name": "insert_count",
     "default": "125000",
     "doc": ""
    },
    {
     "name": "cycle_length",
     "default": "1000000",
     "doc": ""
    },
    {
     "name": "block_bytes",
     "default": "256",
     "doc": ""
    }
   ],
   "chaos": [
    {
     "name": "duration_s",
     "default": "180",
     "doc": ""
    },
    {
     "name": "inject_at_s",
     "default": "60",
     "doc": ""
    },
    {
     "name": "concurrency",
     "default": "100",
     "doc": ""
    },
    {
     "name": "target",
     "default": "'gcp-1'",
     "doc": "Node leading the write path (leaseholder or Patroni primary). On PostgreSQL the live primary is resolved at fault time and overrides this."
    },
    {
     "name": "recovery_threshold",
     "default": "0.8",
     "doc": ""
    },
    {
     "name": "recovery_hold_s",
     "default": "10",
     "doc": ""
    },
    {
     "name": "min_post_fault_s",
     "default": "60",
     "doc": "Minimum generator sampling after the fault (after the heal, in ``recover`` mode). ``duration_s`` is extended to cover it, never shortened."
    },
    {
     "name": "leaseholder_settle_s",
     "default": "300",
     "doc": "How long pre-flight waits for leaseholders to return to the gateway region after a previous chaos run (CockroachDB only)."
    },
    {
     "name": "audit_interval_s",
     "default": "0.02",
     "doc": "RPO audit writer cadence. Its real resolution is bounded by the quorum write cost (~70 ms), which is why the RTO probe exists."
    },
    {
     "name": "probe_enabled",
     "default": "True",
     "doc": ""
    },
    {
     "name": "probe_interval_s",
     "default": "0.002",
     "doc": "Dispatch cadence; the achieved rate is measured per run."
    },
    {
     "name": "probe_workers",
     "default": "8",
     "doc": "In-flight canary writes. Resolution is roughly write cost / workers (~123 ms / 8 = ~21-29 ms from the client node)."
    },
    {
     "name": "probe_statement_timeout_ms",
     "default": "5000",
     "doc": "Generous on purpose: a write that blocks through a failover and then commits is the most precise observation of recovery."
    },
    {
     "name": "probe_connect_timeout_s",
     "default": "2.0",
     "doc": ""
    },
    {
     "name": "probe_table",
     "default": "'rto_canary'",
     "doc": ""
    }
   ],
   "hardware_metrics": [
    {
     "name": "enabled",
     "default": "True",
     "doc": ""
    },
    {
     "name": "sample_interval_s",
     "default": "5.0",
     "doc": "Polling cadence across all six nodes."
    }
   ]
  },
  "profiles": {
   "smoke": {
    "resolved": {
     "name": "smoke",
     "workload": {
      "generator": "ycsb",
      "ycsb_workload": "CUSTOM",
      "read_freq": 0.8,
      "update_freq": 0.2,
      "request_distribution": "uniform",
      "read_percent": 80,
      "duration_s": 15,
      "warmup_s": 3,
      "display_every_s": 1,
      "concurrencies": [
       10,
       50
      ],
      "repetitions": 1,
      "randomise_tier_order": false,
      "cooldown_s": 5,
      "seed": 42,
      "insert_count": 125000,
      "cycle_length": 1000000,
      "block_bytes": 256
     },
     "chaos": {
      "duration_s": 45,
      "inject_at_s": 15,
      "concurrency": 10,
      "target": "gcp-1",
      "recovery_threshold": 0.8,
      "recovery_hold_s": 10,
      "min_post_fault_s": 45,
      "leaseholder_settle_s": 300,
      "audit_interval_s": 0.02,
      "probe_enabled": true,
      "probe_interval_s": 0.002,
      "probe_workers": 2,
      "probe_statement_timeout_ms": 5000,
      "probe_connect_timeout_s": 2.0,
      "probe_table": "rto_canary"
     },
     "hardware_metrics": {
      "enabled": true,
      "sample_interval_s": 5.0
     },
     "tps_ceiling": 20000.0
    },
    "overrides": {
     "workload": [
      "concurrencies",
      "cooldown_s",
      "duration_s",
      "insert_count",
      "randomise_tier_order",
      "repetitions",
      "seed",
      "warmup_s"
     ],
     "chaos": [
      "concurrency",
      "duration_s",
      "inject_at_s",
      "min_post_fault_s",
      "probe_enabled",
      "probe_workers",
      "recovery_threshold",
      "target"
     ],
     "hardware_metrics": []
    },
    "file": "profiles/smoke.yaml"
   },
   "thesis-extended": {
    "resolved": {
     "name": "thesis-extended",
     "workload": {
      "generator": "ycsb",
      "ycsb_workload": "CUSTOM",
      "read_freq": 0.8,
      "update_freq": 0.2,
      "request_distribution": "uniform",
      "read_percent": 80,
      "duration_s": 60,
      "warmup_s": 5,
      "display_every_s": 1,
      "concurrencies": [
       1,
       2,
       5,
       10,
       50,
       100,
       200
      ],
      "repetitions": 3,
      "randomise_tier_order": true,
      "cooldown_s": 15,
      "seed": 42,
      "insert_count": 3750000,
      "cycle_length": 1000000,
      "block_bytes": 256
     },
     "chaos": {
      "duration_s": 180,
      "inject_at_s": 60,
      "concurrency": 100,
      "target": "gcp-1",
      "recovery_threshold": 0.8,
      "recovery_hold_s": 10,
      "min_post_fault_s": 900,
      "leaseholder_settle_s": 900,
      "audit_interval_s": 0.02,
      "probe_enabled": true,
      "probe_interval_s": 0.002,
      "probe_workers": 8,
      "probe_statement_timeout_ms": 5000,
      "probe_connect_timeout_s": 2.0,
      "probe_table": "rto_canary"
     },
     "hardware_metrics": {
      "enabled": true,
      "sample_interval_s": 5.0
     },
     "tps_ceiling": 20000.0
    },
    "overrides": {
     "workload": [
      "block_bytes",
      "concurrencies",
      "cooldown_s",
      "cycle_length",
      "display_every_s",
      "duration_s",
      "generator",
      "insert_count",
      "randomise_tier_order",
      "read_freq",
      "read_percent",
      "repetitions",
      "request_distribution",
      "seed",
      "update_freq",
      "warmup_s",
      "ycsb_workload"
     ],
     "chaos": [
      "audit_interval_s",
      "concurrency",
      "duration_s",
      "inject_at_s",
      "leaseholder_settle_s",
      "min_post_fault_s",
      "probe_connect_timeout_s",
      "probe_enabled",
      "probe_interval_s",
      "probe_statement_timeout_ms",
      "probe_table",
      "probe_workers",
      "recovery_hold_s",
      "recovery_threshold",
      "target"
     ],
     "hardware_metrics": []
    },
    "file": "profiles/thesis-extended.yaml"
   },
   "thesis": {
    "resolved": {
     "name": "thesis",
     "workload": {
      "generator": "ycsb",
      "ycsb_workload": "CUSTOM",
      "read_freq": 0.8,
      "update_freq": 0.2,
      "request_distribution": "uniform",
      "read_percent": 80,
      "duration_s": 60,
      "warmup_s": 5,
      "display_every_s": 1,
      "concurrencies": [
       10,
       50,
       100,
       200
      ],
      "repetitions": 3,
      "randomise_tier_order": true,
      "cooldown_s": 15,
      "seed": 42,
      "insert_count": 3750000,
      "cycle_length": 1000000,
      "block_bytes": 256
     },
     "chaos": {
      "duration_s": 180,
      "inject_at_s": 60,
      "concurrency": 100,
      "target": "gcp-1",
      "recovery_threshold": 0.8,
      "recovery_hold_s": 10,
      "min_post_fault_s": 450,
      "leaseholder_settle_s": 900,
      "audit_interval_s": 0.02,
      "probe_enabled": true,
      "probe_interval_s": 0.002,
      "probe_workers": 8,
      "probe_statement_timeout_ms": 5000,
      "probe_connect_timeout_s": 2.0,
      "probe_table": "rto_canary"
     },
     "hardware_metrics": {
      "enabled": true,
      "sample_interval_s": 5.0
     },
     "tps_ceiling": 20000.0
    },
    "overrides": {
     "workload": [
      "block_bytes",
      "concurrencies",
      "cooldown_s",
      "cycle_length",
      "display_every_s",
      "duration_s",
      "generator",
      "insert_count",
      "randomise_tier_order",
      "read_freq",
      "read_percent",
      "repetitions",
      "request_distribution",
      "seed",
      "update_freq",
      "warmup_s",
      "ycsb_workload"
     ],
     "chaos": [
      "audit_interval_s",
      "concurrency",
      "duration_s",
      "inject_at_s",
      "leaseholder_settle_s",
      "min_post_fault_s",
      "probe_connect_timeout_s",
      "probe_enabled",
      "probe_interval_s",
      "probe_statement_timeout_ms",
      "probe_table",
      "probe_workers",
      "recovery_hold_s",
      "recovery_threshold",
      "target"
     ],
     "hardware_metrics": []
    },
    "file": "profiles/thesis.yaml"
   }
  }
 },
 "schemas": {
  "version": "2.1",
  "csv": [
   {
    "constant": "COLUMNS",
    "file": "metrics.csv",
    "columns": [
     {
      "name": "ts_utc",
      "doc": "Wall-clock time the interval was recorded."
     },
     {
      "name": "elapsed_s",
      "doc": "The generator's own elapsed time, from when it started issuing operations."
     },
     {
      "name": "wall_offset_s",
      "doc": "Harness monotonic clock, seconds from the run's epoch. ~5 s ahead of elapsed_s (SSH and process startup). Blank in schema 2.0 runs."
     },
     {
      "name": "concurrency",
      "doc": "The tier's --concurrency."
     },
     {
      "name": "repetition",
      "doc": "Which repeat of the tier (1-based)."
     },
     {
      "name": "op",
      "doc": "Operation type, e.g. read or update. Latency is never pooled across ops."
     },
     {
      "name": "tps",
      "doc": "Operations per second in this interval, for this op."
     },
     {
      "name": "tps_cum",
      "doc": "Cumulative operations per second since the tier started."
     },
     {
      "name": "errors_cum",
      "doc": "Cumulative error count since the tier started."
     },
     {
      "name": "p50_ms",
      "doc": "Median latency in this interval, for this op."
     },
     {
      "name": "p95_ms",
      "doc": "95th percentile latency."
     },
     {
      "name": "p99_ms",
      "doc": "99th percentile latency."
     },
     {
      "name": "pmax_ms",
      "doc": "Maximum latency."
     },
     {
      "name": "gateway_cpu_pct",
      "doc": "Always blank; per-node utilisation lives in hardware_metrics.csv."
     },
     {
      "name": "gateway_disk_iops",
      "doc": "Always blank; see hardware_metrics.csv."
     },
     {
      "name": "gateway_rss_bytes",
      "doc": "Always blank; see hardware_metrics.csv."
     }
    ]
   },
   {
    "constant": "NETWORK_COLUMNS",
    "file": "network.csv",
    "columns": [
     {
      "name": "ts_utc",
      "doc": "When the pair was probed."
     },
     {
      "name": "source",
      "doc": "Source hostname."
     },
     {
      "name": "destination",
      "doc": "Destination hostname."
     },
     {
      "name": "source_region",
      "doc": "Source region."
     },
     {
      "name": "destination_region",
      "doc": "Destination region."
     },
     {
      "name": "samples",
      "doc": "Ping replies received."
     },
     {
      "name": "loss_pct",
      "doc": "Packet loss percentage."
     },
     {
      "name": "rtt_min_ms",
      "doc": "Minimum RTT, from ping's summary line (3 decimals)."
     },
     {
      "name": "rtt_mean_ms",
      "doc": "Mean RTT, from ping's summary line. The quorum floor is computed from this."
     },
     {
      "name": "rtt_p50_ms",
      "doc": "Median RTT, from per-packet lines."
     },
     {
      "name": "rtt_p95_ms",
      "doc": "95th percentile RTT."
     },
     {
      "name": "rtt_p99_ms",
      "doc": "99th percentile RTT."
     },
     {
      "name": "rtt_max_ms",
      "doc": "Maximum RTT."
     },
     {
      "name": "rtt_mdev_ms",
      "doc": "Mean deviation, from ping's summary line."
     },
     {
      "name": "rtt_resolution_ms",
      "doc": "Precision of the per-packet values (ping prints fewer decimals for slower links)."
     }
    ]
   },
   {
    "constant": "AUDIT_COLUMNS",
    "file": "audit.csv",
    "columns": [
     {
      "name": "wall_offset_s",
      "doc": "Seconds from the run's epoch when the attempt finished."
     },
     {
      "name": "seq_id",
      "doc": "Sequence number, never reused or retried."
     },
     {
      "name": "outcome",
      "doc": "ack (committed), ambiguous (connection failed after sending) or refused (rejected by a reachable database)."
     }
    ]
   },
   {
    "constant": "PROBE_COLUMNS",
    "file": "rto_probe.csv",
    "columns": [
     {
      "name": "ts_utc",
      "doc": "Wall clock at microsecond resolution."
     },
     {
      "name": "seq_id",
      "doc": "Attempt sequence number."
     },
     {
      "name": "dispatch_offset_s",
      "doc": "Seconds from the epoch when the write was sent."
     },
     {
      "name": "complete_offset_s",
      "doc": "Seconds from the epoch when it returned. Recovery timing uses this edge."
     },
     {
      "name": "duration_ms",
      "doc": "complete - dispatch, in ms."
     },
     {
      "name": "outcome",
      "doc": "ok, timeout (the outage signature), conn_error, or refused (a probe bug, not downtime)."
     },
     {
      "name": "worker",
      "doc": "Which worker made the attempt."
     },
     {
      "name": "detail",
      "doc": "Exception text, often empty."
     }
    ]
   },
   {
    "constant": "HARDWARE_METRICS_COLUMNS",
    "file": "hardware_metrics.csv",
    "columns": [
     {
      "name": "ts_utc",
      "doc": "Wall clock of the poll."
     },
     {
      "name": "wall_offset_s",
      "doc": "Seconds from the run's epoch."
     },
     {
      "name": "node",
      "doc": "Short node name (gcp-1, client-1, ...)."
     },
     {
      "name": "host",
      "doc": "Hostname (crdb-gcp-1, ...)."
     },
     {
      "name": "cpu_busy_pct",
      "doc": "100 x (1 - idle/total) since the previous poll. Blank on a node's first poll."
     },
     {
      "name": "cpu_seconds_idle_cum",
      "doc": "Raw idle CPU-seconds counter."
     },
     {
      "name": "cpu_seconds_total_cum",
      "doc": "Raw total CPU-seconds counter."
     },
     {
      "name": "mem_total_bytes",
      "doc": "MemTotal gauge."
     },
     {
      "name": "mem_available_bytes",
      "doc": "MemAvailable gauge."
     },
     {
      "name": "disk_read_bytes_per_s",
      "doc": "Read rate since the previous poll, all devices."
     },
     {
      "name": "disk_write_bytes_per_s",
      "doc": "Write rate since the previous poll, all devices."
     },
     {
      "name": "disk_busy_pct",
      "doc": "100 x delta(io_time) / delta(t)."
     },
     {
      "name": "disk_read_bytes_cum",
      "doc": "Raw read-bytes counter."
     },
     {
      "name": "disk_write_bytes_cum",
      "doc": "Raw written-bytes counter."
     },
     {
      "name": "disk_io_time_seconds_cum",
      "doc": "Raw IO-time counter."
     },
     {
      "name": "net_rx_bytes_per_s",
      "doc": "Receive rate, all non-loopback interfaces."
     },
     {
      "name": "net_tx_bytes_per_s",
      "doc": "Transmit rate, all non-loopback interfaces."
     },
     {
      "name": "net_rx_bytes_cum",
      "doc": "Raw receive counter."
     },
     {
      "name": "net_tx_bytes_cum",
      "doc": "Raw transmit counter."
     },
     {
      "name": "load1",
      "doc": "1-minute load average."
     }
    ]
   }
  ],
  "manifest": [
   {
    "name": "run_id",
    "type": "str"
   },
   {
    "name": "phase",
    "type": "str"
   },
   {
    "name": "schema_version",
    "type": "str"
   },
   {
    "name": "engine",
    "type": "str"
   },
   {
    "name": "started_utc",
    "type": "str"
   },
   {
    "name": "finished_utc",
    "type": "str | None"
   },
   {
    "name": "git_revision",
    "type": "str | None"
   },
   {
    "name": "profile",
    "type": "dict[str, Any]"
   },
   {
    "name": "topology",
    "type": "list[dict[str, Any]]"
   },
   {
    "name": "clock_epoch_utc",
    "type": "str | None"
   },
   {
    "name": "server_version",
    "type": "str | None"
   },
   {
    "name": "cockroach_version",
    "type": "str | None"
   },
   {
    "name": "generator_command",
    "type": "str | None"
   },
   {
    "name": "ssh_options",
    "type": "list[str]"
   },
   {
    "name": "client_platform",
    "type": "str"
   },
   {
    "name": "notes",
    "type": "list[str]"
   },
   {
    "name": "generator_totals",
    "type": "dict[str, Any]"
   },
   {
    "name": "validation",
    "type": "dict[str, Any]"
   }
  ],
  "probe_outcomes": [
   "ok",
   "timeout",
   "conn_error",
   "refused"
  ]
 },
 "charts": {
  "groups": {
   "A": "Benchmark and saturation",
   "B": "Hardware utilisation",
   "C": "Resilience",
   "D": "Engine comparison",
   "E": "Network and provenance"
  },
  "charts": [
   {
    "id": "A1",
    "title": "Throughput-latency curve",
    "caption": "Each point is one concurrency tier. The curve bends upward at the knee, past which offered load buys queueing rather than throughput. Points are ordered by concurrency, not by throughput, because past saturation the curve genuinely bends backwards.",
    "stem": "a1_throughput_latency_curve"
   },
   {
    "id": "A2",
    "title": "Latency percentile fan",
    "caption": "p50, p95, p99 and max for each operation type, per engine, on a log axis. A fan that opens with concurrency is queueing; one that stays parallel is a shifted floor. Operation types are never pooled.",
    "stem": "a2_latency_percentile_fan"
   },
   {
    "id": "A3",
    "title": "Concurrency slot occupancy",
    "caption": "Little's law applied per operation type: an operation occupies throughput x latency of the client's fixed concurrency budget, and both factors are measured per operation rather than assumed from the configured mix. Where reads grow expensive they crowd out writes for the same slots, which is a different statement from either one simply being slow.",
    "stem": "a3_concurrency_slot_occupancy"
   },
   {
    "id": "A4",
    "title": "Steady-state stability",
    "caption": "Within-tier coefficient of variation of throughput. A tier above the line was still moving while it was being recorded, and its mean is a average over a transient rather than a steady state.",
    "stem": "a4_steady_state_stability"
   },
   {
    "id": "A5",
    "title": "Error rate against load",
    "caption": "Errors per tier. The bench sweep runs without --tolerate-errors, so a non-zero count here would mean the generator survived something it was not configured to absorb; zero is the expected result and is what makes the throughput figures quotable.",
    "stem": "a5_error_rate_against_load"
   },
   {
    "id": "A6",
    "title": "Throughput under a latency budget",
    "caption": "The highest measured throughput whose p99 stayed inside each budget. A zero bar means no tier met that budget at all. This restates the same tiers as A1 in the form a service owner buys: peak throughput at an unbounded tail is not deliverable capacity.",
    "stem": "a6_throughput_under_a_latency_budget"
   },
   {
    "id": "A7",
    "title": "Throughput by concurrency",
    "caption": "Mean throughput per tier against the concurrency that produced it. Flattening or falling back past some concurrency is saturation; A1 shows what that saturation costs in latency.",
    "stem": "a7_throughput_by_concurrency"
   },
   {
    "id": "B1",
    "title": "Per-node CPU timeline",
    "caption": "CPU busy per node across the benchmark sweep, one panel per engine. The client node is dotted and is not a cluster member; it is shown so a client-side bottleneck can be excluded rather than assumed away.",
    "stem": "b1_per_node_cpu_timeline"
   },
   {
    "id": "B2",
    "title": "CPU efficiency",
    "caption": "Delivered throughput divided by mean CPU busy across the five cluster nodes, per tier. This is the cost-of-replication figure that throughput alone cannot give: an engine can be faster and still be spending more machine to get there. The client node is excluded from the CPU mean. Note: each tier's CPU is averaged from the start of its first repetition to the end of its last, and repetitions run in shuffled order, so with more than one repetition per tier the CPU side of this ratio is close to the whole sweep's average.",
    "stem": "b2_cpu_efficiency"
   },
   {
    "id": "B3",
    "title": "Disk I/O and busy time",
    "caption": "Mean per-node disk throughput and busy time across the cluster. At the current profile the working set is roughly 1.5x node RAM, so sustained read traffic here is the evidence that the benchmark is disk-bound rather than served from cache -- the assumption the whole profile rests on. A second axis carries busy %; it is dotted and grey because it is a different quantity, not a third series.",
    "stem": "b3_disk_i_o_and_busy_time"
   },
   {
    "id": "B4",
    "title": "Memory headroom",
    "caption": "Mean available memory across the five cluster nodes. Both engines are configured with a cache of a quarter of measured RAM, so this shows the OS page cache absorbing the remainder -- and how little headroom is left once a working set larger than RAM is being served.",
    "stem": "b4_memory_headroom"
   },
   {
    "id": "B5",
    "title": "Replication traffic amplification",
    "caption": "Total bytes transmitted by the five cluster nodes divided by the operations they served, per tier. This is the closest direct measurement the testbed makes of what each replication design costs on the wire -- Raft's per-range replication against Patroni's WAL streaming. It is a ratio of two measured rates, so it is insensitive to the tiers having different durations. Read it as an order of magnitude: the link also carries Tailscale and node exporter traffic, which is not separated out. Note: the traffic is the mean per node, not the five-node total, and each tier's window runs from the start of its first repetition to the end of its last, so with more than one repetition per tier it is close to the whole sweep's average.",
    "stem": "b5_replication_traffic_amplification"
   },
   {
    "id": "B6",
    "title": "Cluster utilisation heatmap",
    "caption": "Every cluster node's CPU on one grid, binned onto a common time axis because the nodes are polled independently. A single bright row is a single busy machine; a uniformly lit panel is work spread across the cluster. One continuous hue, so brightness reads as magnitude rather than as category.",
    "stem": "b6_cluster_utilisation_heatmap"
   },
   {
    "id": "B7",
    "title": "Cluster load imbalance",
    "caption": "Mean CPU per cluster node over the whole sweep, with a scale-free coefficient of variation recorded alongside so that an engine which simply runs hotter everywhere is not counted as imbalanced. Read this against the deployment rather than against the engines' reputations: this testbed deliberately pins CockroachDB's leaseholders to the gateway via lease_preferences and pins Patroni's primary to the same node, so neither arm is free to spread work as it otherwise might. What the chart measures is how much of the cluster each engine still uses under that pinning, which is a property of this configuration and must be quoted as one.",
    "stem": "b7_cluster_load_imbalance"
   },
   {
    "id": "C1",
    "title": "Probe attempt strip",
    "caption": "One vertical rule per canary write, placed at the moment it completed and coloured by outcome. The outage is the blank band: this is the outage as directly observed, with no statistic between the reader and the measurement. Writes are placed by completion time, never dispatch time.",
    "stem": "c1_probe_attempt_strip"
   },
   {
    "id": "C2",
    "title": "Probe latency through the fault",
    "caption": "Duration of every served canary write, log scale. A step in the floor after the fault is a structural change in the write path -- the new primary or leaseholder is a different distance away -- and is a separate finding from how long writes were unavailable.",
    "stem": "c2_probe_latency_through_the_fault"
   },
   {
    "id": "C3",
    "title": "Instrument agreement",
    "caption": "The same outage as measured by two instruments that share no code path, with each one's own sampling resolution drawn as its error bar. Agreement within those bars is the defensibility claim: a single instrument can be wrong in the flattering direction and nothing in its own output would show it.",
    "stem": "c3_instrument_agreement"
   },
   {
    "id": "C4",
    "title": "Recovery decomposition",
    "caption": "The interval from the fault to the first blocked write is detection, not recovery -- the injected command returns before established connections stop working, so writes continue briefly after the fault is nominally in place. Separating the two prevents a fast failover from being credited with a slow detection.",
    "stem": "c4_recovery_decomposition"
   },
   {
    "id": "C5",
    "title": "Post-fault settling",
    "caption": "Throughput through the fault, with the post-fault mean and its one-sigma band. The verdict is the harness's own: a run whose coefficient of variation stays above 0.25 has not settled, and no recovery time can be stated for it -- which is a result to report, not a missing number.",
    "stem": "c5_post_fault_settling"
   },
   {
    "id": "C6",
    "title": "Read and write paths after failover",
    "caption": "Read and update medians either side of the fault, log scale. This is the asymmetry the cross-engine write-up must quote carefully: PostgreSQL's clients follow the primary through HAProxy, so a promotion into another region moves the read path too -- and reads are 80% of this workload. A throughput RTO that never resolves is then a statement about where the new primary landed, not about the write path, and must be quoted with these read medians beside it. Note: the \"after\" median is taken from the moment of the fault, so it includes the outage's own intervals, which record a p50 of 0; where the outage is a large share of what followed the fault, the \"after\" median understates the latency, down to 0.",
    "stem": "c6_read_and_write_paths_after_failover"
   },
   {
    "id": "C7",
    "title": "Hardware through the failover",
    "caption": "Per-node CPU across the fault, with the faulted node in the reserved status colour. The faulted node going quiet confirms the fault landed on the machine it was aimed at, and a survivor rising afterwards is the promotion visible in hardware rather than inferred from the database's own logs.",
    "stem": "c7_hardware_through_the_failover"
   },
   {
    "id": "C8",
    "title": "RTO and RPO summary",
    "caption": "Recovery time from both instruments and acknowledged writes lost, for every engine and fault class. RPO zero is the expected result for a quorum-replicated database and is meaningful only because the measurement could have shown otherwise: the audit client records what it was told committed and advances past ambiguous writes rather than retrying them.",
    "stem": "c8_rto_and_rpo_summary"
   },
   {
    "id": "D1",
    "title": "Engine throughput-latency curves",
    "caption": "Each engine's own curve, annotated with the concurrency that produced each point. Read the horizontal distance as capacity and the vertical as cost; quote the saturation point rather than a single ratio, because the gap between the curves depends entirely on where along them it is measured.",
    "stem": "d1_engine_throughput_latency_curves"
   },
   {
    "id": "D2",
    "title": "Latency at matched throughput",
    "caption": "Both engines evaluated at throughputs each of them genuinely measured -- no extrapolation beyond either curve. The ratio above each pair is the overhead at that load; the starred point is the least confounded one, where the two engines were closest to the same fraction of their own capacity. Do not quote the largest ratio: it is the one where the slower engine is nearest saturation and so carries the most of its own queueing.",
    "stem": "d2_latency_at_matched_throughput"
   },
   {
    "id": "D3",
    "title": "Latency at matched utilisation",
    "caption": "The engines held at the same fraction of their own measured capacity, so their queueing components are comparable and the residual is closer to the replication path alone. The throughputs beneath each pair are deliberately different -- that is what this framing holds variable -- so these bars must never be quoted as a cost at any particular ops/s.",
    "stem": "d3_latency_at_matched_utilisation"
   },
   {
    "id": "D4",
    "title": "Engine scorecard",
    "caption": "Each row normalised to the larger of the two values so that quantities in different units share an axis; the raw value is printed beside every bar because the normalised length alone is not quotable. The direction that counts as better differs by row and is stated on each one.",
    "stem": "d4_engine_scorecard"
   },
   {
    "id": "D5",
    "title": "Cost of consistency",
    "caption": "Each engine's write median at its lightest measured load, against the quorum floor measured independently by ping in Phase I. The floor is what the speed of light and this topology cost before any database is involved -- a 3-of-5 Raft quorum and Patroni's ANY 2 acknowledgement are the same geometry -- so the excess above it is the software's own contribution. Taken at the lightest load because that is where queueing contributes least.",
    "stem": "d5_cost_of_consistency"
   },
   {
    "id": "E1",
    "title": "Quorum floor derivation",
    "caption": "Round-trip time from the gateway to every other node, sorted. A write commits once the leader and the two fastest acknowledgements have it, so the second bar sets the floor and the slower nodes do not gate an ordinary commit at all. Measured with ping, so it is independent of both databases -- which is what lets it bound them.",
    "stem": "e1_quorum_floor_derivation"
   },
   {
    "id": "E2",
    "title": "Link stability",
    "caption": "Minimum to p99 for every ordered pair. A short bar is a link whose mean is a real description of it; a long one is a link whose mean is an average over two different behaviours, and any latency figure resting on it inherits that spread. Note that ping's printed precision degrades as RTT grows -- each link's own resolution is recorded in network.csv.",
    "stem": "e2_link_stability"
   },
   {
    "id": "E3",
    "title": "Run provenance",
    "caption": "Every run directory this report considered. A run reaches a chart only by loading through the gated loader, which refuses anything without a manifest, with unexpected columns, or that fails pre-flight or validation -- so a run listed as refused here contributed to nothing in this document.",
    "stem": "e3_run_provenance"
   },
   {
    "id": "E4",
    "title": "Round-trip matrix",
    "caption": "Mean round-trip time between every ordered pair of nodes, including the two neither E1 nor E2 puts on one axis with the rest: the non-gateway pairs. Darker is slower; a node does not ping itself, shown as a dash.",
    "stem": "e4_round_trip_matrix"
   }
  ]
 }
};

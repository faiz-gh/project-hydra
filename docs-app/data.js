window.HYDRA_DOCS = {
 "modules": [
  {
   "path": "crdblab/__init__.py",
   "module": "crdblab",
   "doc": "",
   "lines": 0,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/__main__.py",
   "module": "crdblab.__main__",
   "doc": "",
   "lines": 3,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/analysis/__init__.py",
   "module": "crdblab.analysis",
   "doc": "",
   "lines": 0,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/analysis/engine_comparison.py",
   "module": "crdblab.analysis.engine_comparison",
   "doc": "CockroachDB vs PostgreSQL/Patroni on the same five-node topology.\n\nBoth engines are replicated (Raft vs. Patroni with ``synchronous_standby_names:\n'ANY 2 (*)'``), so this compares two replication mechanisms.\n\n**Concurrency is not load.** ``--concurrency`` fixes the number of workers,\nnot the work done, so two engines at the same tier sit at different points on\ntheir throughput-latency curves. Comparing them there is invalid (reported\nonly as :func:`same_concurrency_delta`, labelled as such). Instead:\n\n* each engine's **throughput-latency curve**;\n* latency **at matched throughput**, only where the measured ranges overlap;\n* latency **at matched utilisation** (equal fractions of each engine's peak);\n* the **lightest-load write median**, bounded by each engine's quorum round trip.\n\nEvery comparison is gated on\n:func:`crdblab.analysis.validation.check_run_comparability`.",
   "lines": 464,
   "constants": [
    {
     "name": "SATURATION_TOLERANCE",
     "value": "0.05",
     "doc": "Final-tier throughput gain below which a curve counts as saturated; above it the peak is only a lower bound on capacity.",
     "line": 32
    }
   ],
   "classes": [
    {
     "name": "NotComparable",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "Raised when the two runs may not legitimately be compared at all.",
     "line": 35,
     "fields": [],
     "methods": []
    }
   ],
   "functions": [
    {
     "name": "curves",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run, op: str) -> pd.DataFrame",
     "doc": "Both engines' throughput-latency curves for one operation type, one row per tier.",
     "line": 39,
     "decorators": []
    },
    {
     "name": "_saturation",
     "kind": "function",
     "signature": "(tiers: pd.DataFrame) -> dict[str, Any]",
     "doc": "Whether an engine's peak measured throughput is its capacity.\n\nA curve still rising at its highest tier has a peak that is only a lower bound.",
     "line": 50,
     "decorators": []
    },
    {
     "name": "_interpolate",
     "kind": "function",
     "signature": "(curve: pd.DataFrame, tps: float, column: str) -> float | None",
     "doc": "Latency at a given throughput, linearly between the two bracketing tiers.\n\nReturns ``None`` outside the measured range (no extrapolation). Only the\nrising branch up to the peak is used: past saturation one throughput maps\nto two latencies.",
     "line": 80,
     "decorators": []
    },
    {
     "name": "_overlap_remedy",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run) -> str",
     "doc": "Advice on making the two engines' throughput ranges overlap.\n\nIf the slower engine has saturated, more concurrency cannot help; the faster\nengine must be measured at lower concurrency instead.",
     "line": 103,
     "decorators": []
    },
    {
     "name": "matched_throughput",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run, op: str, quantile: str='p50_ms') -> dict[str, Any]",
     "doc": "Latency of both engines at each measured tier throughput inside both ranges.\n\nEach point also carries both engines' utilisation, since matched throughput\nis not matched utilisation; the narrowest gap is ``least_confounded``.",
     "line": 131,
     "decorators": []
    },
    {
     "name": "matched_utilisation",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run, op: str='update', quantile: str='p50_ms') -> dict[str, Any]",
     "doc": "Compare the engines at equal fractions of their own measured capacity.\n\nComplements :func:`matched_throughput`. With different capacities the two\ncannot coincide: matched throughput loads the smaller system harder, while\nmatched utilisation compares two different throughputs. Both are reported,\neach labelled with what it holds fixed.",
     "line": 227,
     "decorators": []
    },
    {
     "name": "lightest_load_write_latency",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run, op: str='update') -> dict[str, Any]",
     "doc": "Each engine's write median at its lowest measured concurrency.\n\nComparable despite differing loads because it approaches each engine's\nquorum round-trip floor. The offered load is reported alongside.",
     "line": 308,
     "decorators": []
    },
    {
     "name": "same_concurrency_delta",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run) -> dict[str, Any]",
     "doc": "The invalid comparison, computed and labelled as invalid.\n\nKept so the intuitive-but-wrong comparison is shown with its reason.",
     "line": 383,
     "decorators": []
    },
    {
     "name": "compare",
     "kind": "function",
     "signature": "(crdb: Run, pg: Run, op: str='update', accept_hardware_difference: bool=False) -> dict[str, Any]",
     "doc": "Full replication-cost comparison, gated on the two runs being comparable.\n\n``accept_hardware_difference`` downgrades a CPU or memory mismatch from a\nrefusal to a recorded warning.",
     "line": 426,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/analysis/loader.py",
   "module": "crdblab.analysis.loader",
   "doc": "The only way analysis code reads a completed run.\n\n:func:`load_run` refuses a run without a manifest, a run that fails\nvalidation, and a run whose pre-flight failed. Aggregation policy lives here\ntoo: throughput is summed across operation types, and latency is never pooled\nacross them.",
   "lines": 282,
   "constants": [
    {
     "name": "QUANTILES",
     "value": "('p50_ms', 'p95_ms', 'p99_ms', 'pmax_ms')",
     "doc": "Quantile columns, in the order the invariant p50 <= p95 <= p99 <= pMax asserts.",
     "line": 24
    }
   ],
   "classes": [
    {
     "name": "RunLoadError",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "Raised when a run cannot be read, or is not fit to be analysed.",
     "line": 27,
     "fields": [],
     "methods": []
    },
    {
     "name": "Run",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "One measurement run, loaded and checked.\n\n``metrics`` is the long-format table as written, one row per (interval, op).",
     "line": 32,
     "fields": [
      {
       "name": "path",
       "type": "Path",
       "default": "",
       "doc": ""
      },
      {
       "name": "manifest",
       "type": "dict[str, Any]",
       "default": "",
       "doc": ""
      },
      {
       "name": "metrics",
       "type": "pd.DataFrame",
       "default": "",
       "doc": ""
      },
      {
       "name": "events",
       "type": "dict[str, Any] | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "preflight",
       "type": "dict[str, Any] | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "report",
       "type": "ValidationReport",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "run_id",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 47,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "phase",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 51,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "schema_version",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 55,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "engine",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "\"cockroachdb\" or \"postgresql\"; older runs without the field were CockroachDB.",
       "line": 59,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "profile",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 64,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "workload",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 68,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "server_command",
       "kind": "function",
       "signature": "(self) -> str | None",
       "doc": "How the server under test was started, from the manifest notes.",
       "line": 72,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "records_wall_clock",
       "kind": "function",
       "signature": "(self) -> bool",
       "doc": "Whether the metrics carry the harness clock (``wall_offset_s``); schema 2.0 runs do not.",
       "line": 80,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "ticks",
       "kind": "function",
       "signature": "(self, warmup_s: float=0.0) -> pd.DataFrame",
       "doc": "Fold the long table to one row per measurement interval.\n\nThroughput is summed across operation types; the error counter takes the\nmaximum. ``weighted_p50_ms`` is a throughput-weighted blend of per-op\nmedians (not itself a median), used for Little's law.",
       "line": 88,
       "decorators": []
      },
      {
       "name": "latency_by_op",
       "kind": "function",
       "signature": "(self, quantiles: Iterable[str]=QUANTILES, warmup_s: float=0.0) -> pd.DataFrame",
       "doc": "Mean of each per-interval quantile, per operation type.\n\n``op`` stays a grouping key, so quantiles are never averaged across ops.",
       "line": 116,
       "decorators": []
      }
     ]
    },
    {
     "name": "NetworkRun",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "A Phase I network measurement.\n\nHas its own schema, so workload validation does not apply; a manifest is\nstill required so the matrix can be tied to a deployment.",
     "line": 235,
     "fields": [
      {
       "name": "path",
       "type": "Path",
       "default": "",
       "doc": ""
      },
      {
       "name": "manifest",
       "type": "dict[str, Any]",
       "default": "",
       "doc": ""
      },
      {
       "name": "links",
       "type": "pd.DataFrame",
       "default": "",
       "doc": ""
      },
      {
       "name": "preflight",
       "type": "dict[str, Any] | None",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "run_id",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 248,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "quorum_floor_ms",
       "kind": "function",
       "signature": "(self) -> float | None",
       "doc": "",
       "line": 252,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "matrix",
       "kind": "function",
       "signature": "(self, value: str='rtt_mean_ms') -> pd.DataFrame",
       "doc": "Square source-by-destination matrix of one measured column.",
       "line": 257,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_read_json",
     "kind": "function",
     "signature": "(path: Path) -> dict[str, Any] | None",
     "doc": "",
     "line": 136,
     "decorators": []
    },
    {
     "name": "resolve_run",
     "kind": "function",
     "signature": "(target: str | Path, runs_dir: Path | None=None) -> Path",
     "doc": "Accept a run directory, a run id, or a path to a metrics file.",
     "line": 142,
     "decorators": []
    },
    {
     "name": "load_run",
     "kind": "function",
     "signature": "(target: str | Path, runs_dir: Path | None=None, require_valid: bool=True) -> Run",
     "doc": "Load a run and refuse to return it unless it is fit to analyse.\n\n``require_valid=False`` is for inspecting a failed run, and for tests.",
     "line": 154,
     "decorators": []
    },
    {
     "name": "load_network_run",
     "kind": "function",
     "signature": "(target: str | Path, runs_dir: Path | None=None) -> NetworkRun",
     "doc": "",
     "line": 262,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/analysis/resilience.py",
   "module": "crdblab.analysis.resilience",
   "doc": "Phases III-IV analysis: recovery time, recovery point, and their limits.\n\n* **Clock alignment.** ``events.json`` uses the harness clock; ``metrics.csv``\n  also has the generator's ``elapsed``. Schema 2.1 runs record both, so the\n  offset is measured; for schema 2.0 runs :func:`align` bounds it and every\n  derived timing becomes an interval.\n* **Two RTOs.** *Availability RTO* (fault to writes resuming, from the audit log\n  and the RTO probe) and *performance RTO* (throughput back to a fraction of\n  baseline, held). If the cluster settles into a new, lower stable state,\n  performance RTO is reported as undefined rather than infinite.\n* **RPO** counts only acknowledged writes that went missing; ambiguous writes\n  are reported separately.\n\nEverything is re-derived from the run's CSVs rather than copied from events.json.",
   "lines": 832,
   "constants": [
    {
     "name": "LIVENESS_SETTLE_S",
     "value": "15.0",
     "doc": "Post-fault interval (failure detection, lease transfer) excluded when describing the settled state. Performance RTO still includes it.",
     "line": 36
    },
    {
     "name": "SETTLED_CV",
     "value": "0.25",
     "doc": "Coefficient of variation below which a post-fault series counts as settled.",
     "line": 39
    },
    {
     "name": "LATENCY_SHIFT_TOLERANCE",
     "value": "0.15",
     "doc": "A settled write latency within this fraction of baseline counts as \"returned\"; beyond it, a structural shift (losing a fast-quorum member is ~2.7x).",
     "line": 43
    }
   ],
   "classes": [
    {
     "name": "AlignmentError",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "Raised when a run's two timelines cannot be related at all.",
     "line": 46,
     "fields": [],
     "methods": []
    },
    {
     "name": "Alignment",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "How the generator's ``elapsed_s`` maps onto the harness clock.\n\n``wall_offset_s = elapsed_s + offset_s``. ``method`` is ``\"measured\"`` when\nboth clocks were recorded, or ``\"bounded\"``, in which case ``offset_s`` is\n``None`` and the true value lies in ``[lower_s, upper_s]``.",
     "line": 51,
     "fields": [
      {
       "name": "method",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "offset_s",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "lower_s",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "upper_s",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "spread_s",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "detail",
       "type": "str",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "exact",
       "kind": "function",
       "signature": "(self) -> bool",
       "doc": "",
       "line": 67,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "uncertainty_s",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "",
       "line": 71,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "to_wall",
       "kind": "function",
       "signature": "(self, elapsed_s: float) -> float | tuple[float, float]",
       "doc": "Place a generator-clock offset on the harness clock.",
       "line": 74,
       "decorators": []
      },
      {
       "name": "to_generator",
       "kind": "function",
       "signature": "(self, wall_offset_s: float) -> float | tuple[float, float]",
       "doc": "Place a harness-clock offset on the generator's clock.",
       "line": 80,
       "decorators": []
      },
      {
       "name": "to_dict",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 86,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_parse_utc",
     "kind": "function",
     "signature": "(stamp: str | None) -> datetime | None",
     "doc": "",
     "line": 98,
     "decorators": []
    },
    {
     "name": "align",
     "kind": "function",
     "signature": "(run: Run) -> Alignment",
     "doc": "Relate the run's two timelines, measuring the offset where possible.\n\nSchema 2.1: the offset is measured per interval and reported with its spread\n(a small spread shows the clocks run at the same rate). Schema 2.0: the\noffset is bounded between 0 and the run's wall-clock envelope minus the\ngenerator's elapsed span.",
     "line": 104,
     "decorators": []
    },
    {
     "name": "fault_offsets",
     "kind": "function",
     "signature": "(run: Run, alignment: Alignment) -> dict[str, Any]",
     "doc": "Where the fault landed, on both clocks.",
     "line": 161,
     "decorators": []
    },
    {
     "name": "degradation_profile",
     "kind": "function",
     "signature": "(run: Run, alignment: Alignment) -> pd.DataFrame",
     "doc": "Throughput against time since the fault, on one clock.\n\nMeasured alignment gives ``since_fault_s``; bounded alignment gives\n``since_fault_lower_s`` and ``since_fault_upper_s`` instead.",
     "line": 190,
     "decorators": []
    },
    {
     "name": "_observation_end",
     "kind": "function",
     "signature": "(run: Run) -> float | None",
     "doc": "The generator's last tick: a lower bound on when the run's window closed.\n\n``None`` if there is no tick series, in which case coverage is not checked.",
     "line": 217,
     "decorators": []
    },
    {
     "name": "availability",
     "kind": "function",
     "signature": "(run: Run) -> dict[str, Any]",
     "doc": "Availability RTO, re-derived from the audit log where it survives.\n\nFalls back to the summary in ``events.json`` for runs without ``audit.csv``.\nThe returned ``claim`` is the quotable sentence; below the audit cadence it\ncontains no number, since the interval is indistinguishable from none.",
     "line": 233,
     "decorators": []
    },
    {
     "name": "probe_availability",
     "kind": "function",
     "signature": "(run: Run) -> dict[str, Any]",
     "doc": "Availability RTO re-derived from the high-frequency probe, if one ran.\n\nReported beside :func:`availability`, never instead of it: separate clients,\nconnections and tables, so agreement is corroboration. Prefer\n``observed_outage_s`` (probe-to-probe) when the two differ, because the\nprobe's own write delay cancels in it.",
     "line": 308,
     "decorators": []
    },
    {
     "name": "performance",
     "kind": "function",
     "signature": "(run: Run, alignment: Alignment) -> dict[str, Any]",
     "doc": "Performance RTO, re-derived from the metrics table.\n\nWith a bounded alignment it runs at both ends of the interval and reports a\nrange. With no recovery it separates a cluster still degrading from one that\nsettled into a new stable state below the threshold (the metric does not apply).",
     "line": 358,
     "decorators": []
    },
    {
     "name": "post_fault_steady_state",
     "kind": "function",
     "signature": "(run: Run, alignment: Alignment) -> dict[str, Any]",
     "doc": "What the cluster settled to after the fault, once detection had completed.\n\nExcludes :data:`LIVENESS_SETTLE_S` after the fault (detection and lease moves).",
     "line": 463,
     "decorators": []
    },
    {
     "name": "write_latency_recovery",
     "kind": "function",
     "signature": "(run: Run, alignment: Alignment, op: str='update') -> dict[str, Any]",
     "doc": "Did the write path itself come back, independent of aggregate throughput.\n\nReads are most of the workload, so a permanently slower write path can hide\nin aggregate throughput. A run can recover on throughput and still show a\n``structural_latency_shift`` here. Settling is judged as in\n:func:`post_fault_steady_state`; baseline is the last 20 pre-fault intervals.",
     "line": 508,
     "decorators": []
    },
    {
     "name": "quorum_geometry",
     "kind": "function",
     "signature": "(run: Run, network_csv: Path | None, topology: Topology=DEFAULT_TOPOLOGY) -> dict[str, Any]",
     "doc": "Why performance RTO may be undefined, derived from measured round trips.\n\nThe write floor is the RTT to the follower that completes quorum. Losing a\nfast follower raises it; losing the leader (the usual case here, since the\ngateway is the target) means a survivor takes over with its own RTT row, so\nevery survivor is evaluated and a range is reported.\n\nThis explains why performance RTO *may* be undefined; it does not predict\nit. The consequence text differs by engine: on PostgreSQL every operation,\nreads included, follows the primary through HAProxy, so a promotion to a\ndistant node also moves the read path.",
     "line": 602,
     "decorators": []
    },
    {
     "name": "rpo",
     "kind": "function",
     "signature": "(run: Run) -> dict[str, Any]",
     "doc": "Recovery point, preserving the three-way classification of every write.",
     "line": 768,
     "decorators": []
    },
    {
     "name": "summarise",
     "kind": "function",
     "signature": "(run: Run, network_csv: Path | None=None, topology: Topology=DEFAULT_TOPOLOGY) -> dict[str, Any]",
     "doc": "Everything Phase III/IV produces, with the limits attached to each figure.",
     "line": 808,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/analysis/steady_state.py",
   "module": "crdblab.analysis.steady_state",
   "doc": "Phase II steady-state aggregation.\n\n* Across operation types, throughput sums and latency is never pooled\n  (applied in :meth:`crdblab.analysis.loader.Run.ticks`).\n* Across time within a tier, throughput and each per-op quantile are averaged.\n* Across repetitions, a mean with a 95% interval is reported.",
   "lines": 183,
   "constants": [
    {
     "name": "_T95",
     "value": "{1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179}",
     "doc": "Two-sided 95% Student's t critical values by degrees of freedom (avoids a SciPy dependency); beyond the table the normal value is used.",
     "line": 20
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "_t95",
     "kind": "function",
     "signature": "(df: int) -> float",
     "doc": "",
     "line": 24,
     "decorators": []
    },
    {
     "name": "confidence_interval",
     "kind": "function",
     "signature": "(values: pd.Series, level: float=0.95) -> dict[str, Any]",
     "doc": "Mean of ``values`` with a Student's t interval, or ``None`` if n < 2.\n\nWith one repetition the half-width is ``None``, never zero.",
     "line": 28,
     "decorators": []
    },
    {
     "name": "steady_state_window",
     "kind": "function",
     "signature": "(run: Run) -> dict[str, Any]",
     "doc": "What part of each tier the recorded rows already represent.\n\nPhase II already drops the warm-up at write time; this shows whether the\nrows are trimmed, so a caller does not trim twice.",
     "line": 50,
     "decorators": []
    },
    {
     "name": "per_repetition",
     "kind": "function",
     "signature": "(run: Run, warmup_s: float=0.0) -> pd.DataFrame",
     "doc": "One row per (concurrency, repetition): the unit a repetition produces.",
     "line": 65,
     "decorators": []
    },
    {
     "name": "per_tier",
     "kind": "function",
     "signature": "(run: Run, warmup_s: float=0.0) -> pd.DataFrame",
     "doc": "One row per concurrency tier, aggregating across repetitions.\n\nThe interval is over repetition means: per-second samples within a run\nare not independent.",
     "line": 84,
     "decorators": []
    },
    {
     "name": "latency_by_op",
     "kind": "function",
     "signature": "(run: Run, warmup_s: float=0.0) -> pd.DataFrame",
     "doc": "Per-operation latency by tier. Operation type is never collapsed.\n\nEach cell is the mean of per-interval quantiles, not a quantile of the run.",
     "line": 113,
     "decorators": []
    },
    {
     "name": "throughput_latency_curve",
     "kind": "function",
     "signature": "(run: Run, op: str, warmup_s: float=0.0) -> pd.DataFrame",
     "doc": "Offered-load curve for one operation type: throughput against latency.\n\nConcurrency fixes the number of workers, not the load, so phases are\ncompared along this curve rather than at equal concurrency.",
     "line": 128,
     "decorators": []
    },
    {
     "name": "summarise",
     "kind": "function",
     "signature": "(run: Run, warmup_s: float=0.0) -> dict[str, Any]",
     "doc": "Everything a results table for this phase needs, as plain data.",
     "line": 153,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/analysis/validation.py",
   "module": "crdblab.analysis.validation",
   "doc": "Post-run validation: is a recorded run internally consistent?\n\nEach check targets an observable symptom of a real parsing or measurement bug.\nThe cross-run checks assert that two runs being compared differ only in the\nvariable under study. ``validate_probe`` checks an RTO probe log.",
   "lines": 658,
   "constants": [
    {
     "name": "DEFAULT_TPS_CEILING",
     "value": "20000.0",
     "doc": "A sample above this is a cumulative total leaking into the per-interval stream.",
     "line": 16
    },
    {
     "name": "_MATCHED_SERVER_FLAGS",
     "value": "('--cache', '--max-sql-memory')",
     "doc": "Server flags that must match between two same-engine runs.",
     "line": 205
    },
    {
     "name": "MEMORY_TOLERANCE",
     "value": "0.05",
     "doc": "Tolerated relative difference in total memory (providers round differently).",
     "line": 208
    },
    {
     "name": "CACHE_EQUIVALENCE_TOLERANCE",
     "value": "0.05",
     "doc": "Tolerated relative difference between CockroachDB's implied cache (``--cache`` x RAM) and PostgreSQL's ``shared_buffers``; both target 25% of RAM.",
     "line": 212
    },
    {
     "name": "_MATCHED_WORKLOAD_KEYS",
     "value": "('generator', 'ycsb_workload', 'read_freq', 'update_freq', 'request_distribution', 'seed', 'insert_count', 'duration_s', 'warmup_s')",
     "doc": "Workload parameters that must match, or the runs did different work.",
     "line": 215
    },
    {
     "name": "PROBE_OUTCOMES",
     "value": "('ok', 'timeout', 'conn_error', 'refused')",
     "doc": "Valid probe outcomes, kept literal so a writer change cannot silently pass.",
     "line": 521
    }
   ],
   "classes": [
    {
     "name": "Finding",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 20,
     "fields": [
      {
       "name": "check",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "severity",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "message",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "detail",
       "type": "dict[str, Any]",
       "default": "field(default_factory=dict)",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "ValidationReport",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 28,
     "fields": [
      {
       "name": "findings",
       "type": "list[Finding]",
       "default": "field(default_factory=list)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "ok",
       "kind": "function",
       "signature": "(self) -> bool",
       "doc": "",
       "line": 32,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "add",
       "kind": "function",
       "signature": "(self, check: str, severity: str, message: str, **detail: Any) -> None",
       "doc": "",
       "line": 35,
       "decorators": []
      },
      {
       "name": "to_dict",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 38,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_ticks",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> pd.DataFrame",
     "doc": "Collapse the long table to one row per (concurrency, repetition, tick).",
     "line": 48,
     "decorators": []
    },
    {
     "name": "check_plausibility",
     "kind": "function",
     "signature": "(df: pd.DataFrame, ceiling: float=DEFAULT_TPS_CEILING) -> list[Finding]",
     "doc": "Cumulative summary rows admitted as per-interval samples.",
     "line": 54,
     "decorators": []
    },
    {
     "name": "check_quantile_ordering",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> list[Finding]",
     "doc": "Latency columns bound to the wrong header positions break p50 <= p95 <= p99 <= pMax.",
     "line": 70,
     "decorators": []
    },
    {
     "name": "check_littles_law",
     "kind": "function",
     "signature": "(df: pd.DataFrame, tolerance: float=0.9) -> list[Finding]",
     "doc": "Little's law: implied mean latency ``N / X`` must not fall below the median.\n\nFor a closed workload of ``N`` workers, ``N / X`` is an upper bound on mean\nlatency. It is compared with the throughput-weighted mean of per-op medians\n(not the slowest op, which would reject sound mixed workloads). It fires when\nthroughput is over-counted or latency is bound to the wrong column.",
     "line": 91,
     "decorators": []
    },
    {
     "name": "check_sample_cadence",
     "kind": "function",
     "signature": "(df: pd.DataFrame, expected_interval_s: float=1.0) -> list[Finding]",
     "doc": "Detect doubled or dropped ticks (irregular gaps between intervals).",
     "line": 144,
     "decorators": []
    },
    {
     "name": "check_op_coverage",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> list[Finding]",
     "doc": "Every interval should report the same set of operation types.",
     "line": 166,
     "decorators": []
    },
    {
     "name": "check_error_monotonicity",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> list[Finding]",
     "doc": "``errors`` is cumulative; a decrease means blocks were interleaved.",
     "line": 184,
     "decorators": []
    },
    {
     "name": "server_flags",
     "kind": "function",
     "signature": "(command: str | None) -> dict[str, str]",
     "doc": "``--flag=value`` pairs from a recorded server command line.",
     "line": 228,
     "decorators": []
    },
    {
     "name": "_server_command",
     "kind": "function",
     "signature": "(manifest: dict[str, Any]) -> str | None",
     "doc": "",
     "line": 241,
     "decorators": []
    },
    {
     "name": "host_hardware",
     "kind": "function",
     "signature": "(manifest: dict[str, Any]) -> dict[str, Any] | None",
     "doc": "The manifest's ``host:`` note as a dict, or ``None`` if unrecorded.",
     "line": 248,
     "decorators": []
    },
    {
     "name": "pg_cache_config",
     "kind": "function",
     "signature": "(manifest: dict[str, Any]) -> dict[str, int | None] | None",
     "doc": "The ``pg memory:`` note as a dict, or ``None`` for a run without one.\n\nAbsent for CockroachDB runs, and for PostgreSQL runs whose probe failed.",
     "line": 266,
     "decorators": []
    },
    {
     "name": "check_run_comparability",
     "kind": "function",
     "signature": "(a: dict[str, Any], b: dict[str, Any], label_a: str='A', label_b: str='B', accept_hardware_difference: bool=False) -> list[Finding]",
     "doc": "Assert that two runs differ only in the variable under study.\n\nCompares manifests: workload parameters, server flags (or, across\nengines, cache budgets), host hardware and server version.",
     "line": 286,
     "decorators": []
    },
    {
     "name": "validate_comparison",
     "kind": "function",
     "signature": "(a: dict[str, Any], b: dict[str, Any], label_a: str='A', label_b: str='B', accept_hardware_difference: bool=False) -> ValidationReport",
     "doc": "Report on whether two runs may legitimately be compared.",
     "line": 505,
     "decorators": []
    },
    {
     "name": "check_probe_ordering",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> list[Finding]",
     "doc": "A write cannot complete before dispatch, nor be dispatched before the epoch.",
     "line": 524,
     "decorators": []
    },
    {
     "name": "check_probe_outcomes",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> list[Finding]",
     "doc": "Every outcome must be known, and at least one write served.\n\n``refused`` writes are a probe defect, not downtime, so they are warned about.",
     "line": 552,
     "decorators": []
    },
    {
     "name": "check_probe_sequence",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> list[Finding]",
     "doc": "No sequence number is used twice (a retry would double-count an observation).\n\nGaps are expected: numbers queued at the end of a run are never attempted.",
     "line": 596,
     "decorators": []
    },
    {
     "name": "validate_probe",
     "kind": "function",
     "signature": "(df: pd.DataFrame) -> ValidationReport",
     "doc": "Consistency checks for an ``rto_probe.csv`` (separate schema from metrics).",
     "line": 616,
     "decorators": []
    },
    {
     "name": "validate",
     "kind": "function",
     "signature": "(df: pd.DataFrame, tps_ceiling: float=DEFAULT_TPS_CEILING) -> ValidationReport",
     "doc": "Run every single-run check on a metrics table.",
     "line": 646,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/cli.py",
   "module": "crdblab.cli",
   "doc": "Command-line entry point for ``crdblab``.\n\nUses stdlib ``argparse`` to keep the measurement path's dependencies minimal.",
   "lines": 1160,
   "constants": [],
   "classes": [],
   "functions": [
    {
     "name": "_generator_flags",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> str",
     "doc": "Build the generator-specific flags of a ``cockroach workload run`` command.\n\n``--seed`` is always passed: without the seed used at load time every lookup\nmatches no rows, which looks like a fast, healthy result. ``CUSTOM`` keeps\nthe 80/20 read/update mix, and the key distribution is pinned to ``uniform``.",
     "line": 21,
     "decorators": []
    },
    {
     "name": "_cmd_capture",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "Capture raw generator output and report the column layout it uses.\n\nRun once against a new deployment, before any sweep, to confirm the layout\nthe installed CockroachDB version emits.",
     "line": 47,
     "decorators": []
    },
    {
     "name": "_cmd_net_probe",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "Phase I: measure the all-pairs RTT matrix and derive the quorum floor.\n\nMust run before any benchmark, whose write-latency floor check needs it.",
     "line": 100,
     "decorators": []
    },
    {
     "name": "_latest_network_run",
     "kind": "function",
     "signature": "(runs_dir: Path) -> Path | None",
     "doc": "Most recent Phase I matrix, which supplies the quorum floor.",
     "line": 159,
     "decorators": []
    },
    {
     "name": "_cmd_bench",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "",
     "line": 165,
     "decorators": []
    },
    {
     "name": "_cmd_chaos",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "",
     "line": 220,
     "decorators": []
    },
    {
     "name": "_cmd_analyze",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "Run one analysis. Every run loads through ``load_run``, which refuses\nruns without a manifest or that fail validation.",
     "line": 344,
     "decorators": []
    },
    {
     "name": "_latest_run",
     "kind": "function",
     "signature": "(runs_dir: Path, suffix: str) -> str | None",
     "doc": "Most recent run directory of a given phase.",
     "line": 607,
     "decorators": []
    },
    {
     "name": "_cmd_report",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "Render the dissertation figures, defaulting to the newest run of each phase.\n\nEvery input loads through the analysis loader, so only validated runs are drawn.",
     "line": 613,
     "decorators": []
    },
    {
     "name": "_cmd_validate",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "",
     "line": 659,
     "decorators": []
    },
    {
     "name": "_cmd_probe",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "Run the RTO probe standalone, with no workload generator.\n\nUseful to check the probe's achieved rate and resolution before a chaos run,\nor to time an outage caused outside the harness. Writes a normal run directory.",
     "line": 726,
     "decorators": []
    },
    {
     "name": "_cmd_insights",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "Render the chart catalogue into a fresh ``<out>/<stamp>_<profile|all>/``.",
     "line": 838,
     "decorators": []
    },
    {
     "name": "_cmd_profile",
     "kind": "function",
     "signature": "(args: argparse.Namespace) -> int",
     "doc": "",
     "line": 880,
     "decorators": []
    },
    {
     "name": "build_parser",
     "kind": "function",
     "signature": "() -> argparse.ArgumentParser",
     "doc": "",
     "line": 886,
     "decorators": []
    },
    {
     "name": "main",
     "kind": "function",
     "signature": "(argv: list[str] | None=None) -> int",
     "doc": "",
     "line": 1153,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/config.py",
   "module": "crdblab.config",
   "doc": "Experiment profiles (``profiles/*.yaml``) and runtime settings.\n\nThe resolved profile is copied verbatim into every run manifest.",
   "lines": 255,
   "constants": [
    {
     "name": "PROJECT_ROOT",
     "value": "Path(__file__).resolve().parent.parent",
     "doc": "",
     "line": 18
    },
    {
     "name": "DEFAULT_PROFILE_DIR",
     "value": "PROJECT_ROOT / 'profiles'",
     "doc": "",
     "line": 19
    },
    {
     "name": "DEFAULT_RUNS_DIR",
     "value": "PROJECT_ROOT / 'runs'",
     "doc": "",
     "line": 20
    },
    {
     "name": "DEFAULT_PG_PASSWORD",
     "value": "'rootpassword'",
     "doc": "Password of the ``root`` role created by ``bootstrap-patroni.tftpl``.",
     "line": 23
    },
    {
     "name": "PG_SQL_PORT",
     "value": "5432",
     "doc": "PostgreSQL port on each cluster node (``Node.sql_port`` is CockroachDB's).",
     "line": 26
    },
    {
     "name": "PG_GENERATOR_HOSTPORT",
     "value": "'127.0.0.1:6432'",
     "doc": "The client node's pgbouncer, which forwards to HAProxy and on to the primary.",
     "line": 29
    },
    {
     "name": "PG_CONNECT_TIMEOUT_S",
     "value": "2",
     "doc": "Per-host connect bound in a multi-host DSN. 2 s is libpq's minimum and well above the testbed's worst RTT (~230 ms).",
     "line": 33
    },
    {
     "name": "PG_TCP_USER_TIMEOUT_MS",
     "value": "10000",
     "doc": "How long an established connection may go unanswered before the kernel drops it. Looser than the probe's 5 s statement timeout; see ``pg_direct_dsn``.",
     "line": 37
    },
    {
     "name": "PG_KEEPALIVE_IDLE_S",
     "value": "2",
     "doc": "TCP keepalives, so a black-holed connection is detected rather than waited on.",
     "line": 40
    },
    {
     "name": "PG_KEEPALIVE_INTERVAL_S",
     "value": "2",
     "doc": "",
     "line": 41
    },
    {
     "name": "PG_KEEPALIVE_COUNT",
     "value": "3",
     "doc": "",
     "line": 42
    },
    {
     "name": "PG_HAPROXY_HOSTPORT",
     "value": "'127.0.0.1:5000'",
     "doc": "The client node's HAProxy, which follows Patroni's leader. Used for ``DB_URI`` (psql, data loading, ``capture``).",
     "line": 87
    },
    {
     "name": "DEFAULT_ENV_FILE",
     "value": "PROJECT_ROOT / '.env'",
     "doc": "",
     "line": 109
    }
   ],
   "classes": [
    {
     "name": "WorkloadSpec",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 128,
     "fields": [
      {
       "name": "generator",
       "type": "str",
       "default": "'ycsb'",
       "doc": ""
      },
      {
       "name": "ycsb_workload",
       "type": "str",
       "default": "'CUSTOM'",
       "doc": "ycsb mix: CUSTOM with an explicit 80/20 read/update split, uniform keys."
      },
      {
       "name": "read_freq",
       "type": "float",
       "default": "0.8",
       "doc": ""
      },
      {
       "name": "update_freq",
       "type": "float",
       "default": "0.2",
       "doc": ""
      },
      {
       "name": "request_distribution",
       "type": "str",
       "default": "'uniform'",
       "doc": ""
      },
      {
       "name": "read_percent",
       "type": "int",
       "default": "80",
       "doc": "kv only."
      },
      {
       "name": "duration_s",
       "type": "int",
       "default": "60",
       "doc": ""
      },
      {
       "name": "warmup_s",
       "type": "int",
       "default": "5",
       "doc": ""
      },
      {
       "name": "display_every_s",
       "type": "int",
       "default": "1",
       "doc": ""
      },
      {
       "name": "concurrencies",
       "type": "tuple[int, ...]",
       "default": "(10, 50, 100, 200)",
       "doc": ""
      },
      {
       "name": "repetitions",
       "type": "int",
       "default": "3",
       "doc": ""
      },
      {
       "name": "randomise_tier_order",
       "type": "bool",
       "default": "True",
       "doc": ""
      },
      {
       "name": "cooldown_s",
       "type": "int",
       "default": "15",
       "doc": ""
      },
      {
       "name": "seed",
       "type": "int",
       "default": "42",
       "doc": ""
      },
      {
       "name": "insert_count",
       "type": "int",
       "default": "125000",
       "doc": ""
      },
      {
       "name": "cycle_length",
       "type": "int",
       "default": "1000000",
       "doc": ""
      },
      {
       "name": "block_bytes",
       "type": "int",
       "default": "256",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "expected_ticks_per_tier",
       "kind": "function",
       "signature": "(self) -> int",
       "doc": "",
       "line": 153,
       "decorators": [
        "property"
       ]
      }
     ]
    },
    {
     "name": "ChaosSpec",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 158,
     "fields": [
      {
       "name": "duration_s",
       "type": "int",
       "default": "180",
       "doc": ""
      },
      {
       "name": "inject_at_s",
       "type": "int",
       "default": "60",
       "doc": ""
      },
      {
       "name": "concurrency",
       "type": "int",
       "default": "100",
       "doc": ""
      },
      {
       "name": "target",
       "type": "str",
       "default": "'gcp-1'",
       "doc": "Node leading the write path (leaseholder or Patroni primary). On PostgreSQL the live primary is resolved at fault time and overrides this."
      },
      {
       "name": "recovery_threshold",
       "type": "float",
       "default": "0.8",
       "doc": ""
      },
      {
       "name": "recovery_hold_s",
       "type": "int",
       "default": "10",
       "doc": ""
      },
      {
       "name": "min_post_fault_s",
       "type": "int",
       "default": "60",
       "doc": "Minimum generator sampling after the fault (after the heal, in ``recover`` mode). ``duration_s`` is extended to cover it, never shortened."
      },
      {
       "name": "leaseholder_settle_s",
       "type": "int",
       "default": "300",
       "doc": "How long pre-flight waits for leaseholders to return to the gateway region after a previous chaos run (CockroachDB only)."
      },
      {
       "name": "audit_interval_s",
       "type": "float",
       "default": "0.02",
       "doc": "RPO audit writer cadence. Its real resolution is bounded by the quorum write cost (~70 ms), which is why the RTO probe exists."
      },
      {
       "name": "probe_enabled",
       "type": "bool",
       "default": "True",
       "doc": ""
      },
      {
       "name": "probe_interval_s",
       "type": "float",
       "default": "0.002",
       "doc": "Dispatch cadence; the achieved rate is measured per run."
      },
      {
       "name": "probe_workers",
       "type": "int",
       "default": "8",
       "doc": "In-flight canary writes. Resolution is roughly write cost / workers (~123 ms / 8 = ~21-29 ms from the client node)."
      },
      {
       "name": "probe_statement_timeout_ms",
       "type": "int",
       "default": "5000",
       "doc": "Generous on purpose: a write that blocks through a failover and then commits is the most precise observation of recovery."
      },
      {
       "name": "probe_connect_timeout_s",
       "type": "float",
       "default": "2.0",
       "doc": ""
      },
      {
       "name": "probe_table",
       "type": "str",
       "default": "'rto_canary'",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "HardwareMetricsSpec",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "Per-node CPU/memory/disk/network polling during Phase II-IV.",
     "line": 193,
     "fields": [
      {
       "name": "enabled",
       "type": "bool",
       "default": "True",
       "doc": ""
      },
      {
       "name": "sample_interval_s",
       "type": "float",
       "default": "5.0",
       "doc": "Polling cadence across all six nodes."
      }
     ],
     "methods": []
    },
    {
     "name": "Profile",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 202,
     "fields": [
      {
       "name": "name",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "workload",
       "type": "WorkloadSpec",
       "default": "field(default_factory=WorkloadSpec)",
       "doc": ""
      },
      {
       "name": "chaos",
       "type": "ChaosSpec",
       "default": "field(default_factory=ChaosSpec)",
       "doc": ""
      },
      {
       "name": "hardware_metrics",
       "type": "HardwareMetricsSpec",
       "default": "field(default_factory=HardwareMetricsSpec)",
       "doc": ""
      },
      {
       "name": "tps_ceiling",
       "type": "float",
       "default": "20000.0",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "to_dict",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 209,
       "decorators": []
      },
      {
       "name": "load",
       "kind": "function",
       "signature": "(cls, name_or_path: str) -> Profile",
       "doc": "",
       "line": 213,
       "decorators": [
        "classmethod"
       ]
      }
     ]
    },
    {
     "name": "Settings",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 236,
     "fields": [
      {
       "name": "db_uri",
       "type": "str | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "runs_dir",
       "type": "Path",
       "default": "DEFAULT_RUNS_DIR",
       "doc": ""
      },
      {
       "name": "topology",
       "type": "Topology",
       "default": "field(default_factory=lambda: DEFAULT_TOPOLOGY)",
       "doc": ""
      },
      {
       "name": "pg_password",
       "type": "str",
       "default": "DEFAULT_PG_PASSWORD",
       "doc": "``root`` password for PostgreSQL (``PG_PASSWORD`` in ``.env``). Must match ``terraform/scripts/bootstrap-patroni.tftpl``."
      }
     ],
     "methods": [
      {
       "name": "from_env",
       "kind": "function",
       "signature": "(cls) -> Settings",
       "doc": "",
       "line": 245,
       "decorators": [
        "classmethod"
       ]
      },
      {
       "name": "require_db_uri",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 252,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "pg_generator_dsn",
     "kind": "function",
     "signature": "(database: str, password: str) -> str",
     "doc": "Connection string for the generator: the client node's local pgbouncer.\n\n``cockroach workload`` accepts only one URL, and HAProxy makes that URL\nfollow a failover. pgbouncer strips the ``allow_unsafe_internals`` startup\nparameter the generator sends, which PostgreSQL would otherwise reject.",
     "line": 45,
     "decorators": []
    },
    {
     "name": "pg_direct_dsn",
     "kind": "function",
     "signature": "(topology: Topology, database: str, password: str) -> str",
     "doc": "Multi-host DSN for the audit writer and RTO probe, bypassing HAProxy.\n\nlibpq tries each host in turn and keeps the writable one\n(``target_session_attrs=read-write``). The gateway goes first because it is\nthe designated primary.\n\nConnections are bounded at the TCP layer (connect timeout, keepalives,\n``tcp_user_timeout``) so a partition that black-holes an open socket is\ndetected instead of silently stalling the instrument. A tighter statement\ntimeout is avoided on purpose: a write that blocks through a failover and\nthen commits is the most precise observation of recovery.",
     "line": 58,
     "decorators": []
    },
    {
     "name": "default_db_uri",
     "kind": "function",
     "signature": "(engine: str, topology: Topology, password: str) -> str",
     "doc": "Derive ``DB_URI`` for the deployed engine.\n\n* **cockroachdb**: every cluster member, gateway first, each with ``:26257``.\n* **postgresql**: the client node's HAProxy, with the ``root`` password.",
     "line": 90,
     "decorators": []
    },
    {
     "name": "load_env_file",
     "kind": "function",
     "signature": "(path: Path | None=None) -> bool",
     "doc": "Load the project's ``.env`` into the environment.\n\nResolved relative to the package, not the working directory. Existing\nenvironment variables win. Returns ``True`` if a file was read.",
     "line": 112,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/__init__.py",
   "module": "crdblab.core",
   "doc": "",
   "lines": 0,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/core/hardware_metrics.py",
   "module": "crdblab.core.hardware_metrics",
   "doc": "Per-node CPU/memory/disk/network utilisation, polled from node_exporter.\n\nEvery node runs ``prometheus-node-exporter`` on ``:9100``; it is polled\ndirectly over HTTP. node_exporter exposes monotonic counters, so rates come\nfrom differencing consecutive scrapes, and a node's first poll has no rate\n(written as ``\"\"``, not 0).",
   "lines": 248,
   "constants": [
    {
     "name": "_LINE_RE",
     "value": "re.compile('^(?P<name>node_[A-Za-z0-9_]+)(\\\\{(?P<labels>[^}]*)\\\\})?\\\\s+(?P<value>\\\\S+)\\\\s*$')",
     "doc": "One Prometheus text line for a ``node_*`` metric. A minimal parser is enough for the few families read here.",
     "line": 26
    },
    {
     "name": "_LABEL_RE",
     "value": "re.compile('(?P<key>[A-Za-z0-9_]+)=\"(?P<value>[^\"]*)\"')",
     "doc": "",
     "line": 29
    },
    {
     "name": "_WANTED",
     "value": "frozenset({'node_cpu_seconds_total', 'node_memory_MemTotal_bytes', 'node_memory_MemAvailable_bytes', 'node_disk_read_bytes_total', 'node_disk_written_bytes_t...",
     "doc": "The only metric families read; everything else in a scrape is ignored.",
     "line": 32
    },
    {
     "name": "_Families",
     "value": "dict[str, list[tuple[dict[str, str], float]]]",
     "doc": "name -> [(labels, value), ...], unaggregated; the sampler applies per-family aggregation (all cores, all disks, all non-loopback NICs).",
     "line": 48
    }
   ],
   "classes": [
    {
     "name": "_RawCounters",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "One node's cumulative counters at one scrape, plus when it was taken.",
     "line": 94,
     "fields": [
      {
       "name": "ts",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "cpu_idle",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "cpu_total",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "disk_read",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "disk_write",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "disk_io_time",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "net_rx",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "net_tx",
       "type": "float",
       "default": "",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "HardwareMetricsSampler",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "Polls every node's node_exporter in the background; writes one CSV at the end.\n\nA failed scrape counts against that node in :attr:`scrape_failures` and\nproduces no row for that tick, without affecting other nodes.",
     "line": 131,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, nodes: Sequence[Node], t_zero: float, interval_s: float=5.0, timeout_s: float=2.0) -> None",
       "doc": "",
       "line": 138,
       "decorators": []
      },
      {
       "name": "_sample_node",
       "kind": "function",
       "signature": "(self, node: Node) -> dict[str, Any] | None",
       "doc": "",
       "line": 158,
       "decorators": []
      },
      {
       "name": "_tick",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 218,
       "decorators": []
      },
      {
       "name": "_loop",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 224,
       "decorators": []
      },
      {
       "name": "__enter__",
       "kind": "function",
       "signature": "(self) -> HardwareMetricsSampler",
       "doc": "",
       "line": 233,
       "decorators": []
      },
      {
       "name": "__exit__",
       "kind": "function",
       "signature": "(self, *exc) -> None",
       "doc": "",
       "line": 239,
       "decorators": []
      },
      {
       "name": "write",
       "kind": "function",
       "signature": "(self, path: Path) -> None",
       "doc": "",
       "line": 246,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_parse_labels",
     "kind": "function",
     "signature": "(text: str) -> dict[str, str]",
     "doc": "",
     "line": 51,
     "decorators": []
    },
    {
     "name": "_parse",
     "kind": "function",
     "signature": "(body: str) -> _Families",
     "doc": "",
     "line": 55,
     "decorators": []
    },
    {
     "name": "_sum_family",
     "kind": "function",
     "signature": "(families: _Families, name: str, label_filter: Callable[[dict[str, str]], bool] | None=None) -> float",
     "doc": "",
     "line": 76,
     "decorators": []
    },
    {
     "name": "_gauge",
     "kind": "function",
     "signature": "(families: _Families, name: str) -> float | None",
     "doc": "",
     "line": 88,
     "decorators": []
    },
    {
     "name": "_not_loopback",
     "kind": "function",
     "signature": "(labels: dict[str, str]) -> bool",
     "doc": "",
     "line": 107,
     "decorators": []
    },
    {
     "name": "_extract_counters",
     "kind": "function",
     "signature": "(families: _Families) -> _RawCounters",
     "doc": "",
     "line": 111,
     "decorators": []
    },
    {
     "name": "_rate",
     "kind": "function",
     "signature": "(curr: float, prev: float, dt: float) -> float | str",
     "doc": "",
     "line": 127,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/preflight.py",
   "module": "crdblab.core.preflight",
   "doc": "Checks that the testbed is fit to be measured, run before each measurement.\n\n``analysis/validation.py`` asks whether recorded numbers are consistent with\neach other. Pre-flight asks whether the system was in a state worth measuring,\nwhich catches runs that are arithmetically sound but meaningless (a misplaced\nleaseholder, a workload whose lookups match no rows). Each check records the\nvalue it observed.",
   "lines": 936,
   "constants": [
    {
     "name": "MAX_CLOCK_OFFSET_S",
     "value": "0.25",
     "doc": "Half of CockroachDB's default ``--max-offset`` (500 ms).",
     "line": 24
    },
    {
     "name": "MIN_ROW_MATCH_RATE",
     "value": "0.99",
     "doc": "Below this, the generator's keyspace does not match the loaded data.",
     "line": 27
    },
    {
     "name": "CONTROL_TIMEOUT_S",
     "value": "60",
     "doc": "Hang detector for control-plane SSH commands (not a latency budget; WAN SSH setup alone can take several seconds).",
     "line": 31
    },
    {
     "name": "_SERVER_PROBES",
     "value": "{'cockroachdb': ('pgrep -a cockroach | head -1', 'cockroach version --build-tag 2>/dev/null'), 'postgresql': ('pgrep -a -f bin/[p]ostgres | head -1', 'for b ...",
     "doc": "Shell commands reading the server's argv and version, per engine.",
     "line": 86
    },
    {
     "name": "_PG_MEMORY_QUERY",
     "value": "\"SELECT pg_size_bytes(current_setting('shared_buffers')), pg_size_bytes(current_setting('effective_cache_size'))\"",
     "doc": "Patroni sets these in postgresql.conf, not argv, so they are read via SQL.",
     "line": 151
    },
    {
     "name": "_SYSTEM_TIME_RE",
     "value": "re.compile('System time\\\\s*:\\\\s*([0-9.]+)\\\\s+seconds')",
     "doc": "",
     "line": 200
    },
    {
     "name": "PATRONI_PRIMARY_PORT",
     "value": "8008",
     "doc": "Patroni REST API; ``/primary`` answers 200 only on the leader.",
     "line": 337
    },
    {
     "name": "PATRONI_PRIMARY_TIMEOUT_S",
     "value": "3.0",
     "doc": "",
     "line": 338
    },
    {
     "name": "PATRONI_SWITCHOVER_TIMEOUT_S",
     "value": "120",
     "doc": "A switchover is a controlled handover bounded by replication lag.",
     "line": 341
    },
    {
     "name": "PATRONI_CANDIDATE_POLL_S",
     "value": "5.0",
     "doc": "Candidate re-check interval, under Patroni's 10 s ``loop_wait``.",
     "line": 344
    },
    {
     "name": "_ALLOW_INTERNALS",
     "value": "'SET allow_unsafe_internals = true;'",
     "doc": "``crdb_internal`` may be gated behind this session variable; it is set only for this read-only statistics query, never for workload connections.",
     "line": 603
    },
    {
     "name": "_STATS_QUERY",
     "value": "\"SELECT coalesce(sum((statistics->'statistics'->>'cnt')::float), 0), coalesce(sum((statistics->'statistics'->>'cnt')::float * (statistics->'statistics'->'row...",
     "doc": "",
     "line": 605
    },
    {
     "name": "_PG_STATS_QUERY",
     "value": "\"SELECT coalesce(idx_scan, 0), coalesce(idx_tup_fetch, 0), coalesce(seq_scan, 0) FROM pg_stat_user_tables WHERE relname = '{table}'\"",
     "doc": "PostgreSQL row-match counters: ``idx_tup_fetch / idx_scan`` is the match rate. ``seq_tup_read`` counts rows read, not matched, so a ``seq_scan`` is flagged instead.",
     "line": 739
    }
   ],
   "classes": [
    {
     "name": "PreflightError",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "Raised when the testbed is not in a state worth measuring.",
     "line": 34,
     "fields": [],
     "methods": []
    },
    {
     "name": "Check",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 39,
     "fields": [
      {
       "name": "name",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "passed",
       "type": "bool",
       "default": "",
       "doc": ""
      },
      {
       "name": "detail",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "observed",
       "type": "dict[str, Any]",
       "default": "field(default_factory=dict)",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "PreflightReport",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 47,
     "fields": [
      {
       "name": "checks",
       "type": "list[Check]",
       "default": "field(default_factory=list)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "ok",
       "kind": "function",
       "signature": "(self) -> bool",
       "doc": "",
       "line": 51,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "add",
       "kind": "function",
       "signature": "(self, name: str, passed: bool, detail: str, **observed: Any) -> Check",
       "doc": "",
       "line": 54,
       "decorators": []
      },
      {
       "name": "raise_if_failed",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 59,
       "decorators": []
      },
      {
       "name": "to_dict",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 68,
       "decorators": []
      }
     ]
    },
    {
     "name": "RowMatchProbe",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "Measures what fraction of the workload's statements touched a row (CockroachDB).\n\nIf the generator's seed differs from the one used at load time, every\nlookup matches nothing and the run reports far higher throughput at far\nlower latency. Counters from ``crdb_internal.statement_statistics`` are\ndifferenced across the tier window.",
     "line": 615,
     "fields": [
      {
       "name": "gateway",
       "type": "Node",
       "default": "",
       "doc": ""
      },
      {
       "name": "table",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "_before",
       "type": "tuple[float, float] | None",
       "default": "field(default=None, init=False, repr=False)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "_sample",
       "kind": "function",
       "signature": "(self) -> tuple[float, float]",
       "doc": "",
       "line": 628,
       "decorators": []
      },
      {
       "name": "start",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 648,
       "decorators": []
      },
      {
       "name": "finish",
       "kind": "function",
       "signature": "(self, report: PreflightReport, corroborated: bool=False) -> float | None",
       "doc": "Assert the row-match rate for the tier just measured.\n\n``corroborated`` (the write median cleared the quorum floor) is only\nconsulted when a statistics flush destroyed the tier's evidence.",
       "line": 651,
       "decorators": []
      }
     ]
    },
    {
     "name": "PostgresRowMatchProbe",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "PostgreSQL counterpart of :class:`RowMatchProbe`.\n\nReads ``pg_stat_user_tables`` on the primary (the DSN must resolve to it)\nand differences it across the tier. These counters are not flushed on a\ntimer, so a reset means the server restarted.",
     "line": 746,
     "fields": [
      {
       "name": "exec_node",
       "type": "Node",
       "default": "",
       "doc": ""
      },
      {
       "name": "dsn",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "table",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "password",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "_before",
       "type": "tuple[float, float] | None",
       "default": "field(default=None, init=False, repr=False)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "_sample",
       "kind": "function",
       "signature": "(self) -> tuple[float, float, float]",
       "doc": "",
       "line": 760,
       "decorators": []
      },
      {
       "name": "start",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 784,
       "decorators": []
      },
      {
       "name": "finish",
       "kind": "function",
       "signature": "(self, report: PreflightReport, corroborated: bool=False) -> float | None",
       "doc": "Assert the row-match rate for the tier just measured.\n\n``corroborated`` is consulted only if the counters went backwards.",
       "line": 787,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "capture_server_config",
     "kind": "function",
     "signature": "(node: Node, engine: str='cockroachdb') -> dict[str, Any]",
     "doc": "Record how the database server was started on ``node``, and on what hardware.\n\nCaptures the raw server argv, version, CPU count/model and ``MemTotal`` so\ncomparability checks can spot differences in flags or machines. Cache\nflags are fractions of RAM, so memory matters even when flags match. For\nPostgreSQL, ``memory`` also carries ``shared_buffers``/``effective_cache_size``.",
     "line": 103,
     "decorators": []
    },
    {
     "name": "parse_hardware",
     "kind": "function",
     "signature": "(block: str) -> dict[str, Any]",
     "doc": "Interpret the ``nproc`` / ``model name`` / ``MemTotal`` block.\n\nMissing fields are ``None``, never a plausible default that could compare equal.",
     "line": 132,
     "decorators": []
    },
    {
     "name": "capture_pg_memory_config",
     "kind": "function",
     "signature": "(node: Node) -> dict[str, int] | None",
     "doc": "PostgreSQL's ``shared_buffers``/``effective_cache_size`` in kB, or ``None``.\n\nThe counterpart of CockroachDB's ``--cache`` for cross-engine comparability.\nRead over the local socket as ``postgres``; any failure returns ``None``.",
     "line": 157,
     "decorators": []
    },
    {
     "name": "format_pg_memory",
     "kind": "function",
     "signature": "(memory: dict[str, Any]) -> str",
     "doc": "Render a PostgreSQL memory capture as one manifest note line.",
     "line": 181,
     "decorators": []
    },
    {
     "name": "format_hardware",
     "kind": "function",
     "signature": "(hardware: dict[str, Any]) -> str",
     "doc": "Render a hardware capture as one manifest note line.",
     "line": 189,
     "decorators": []
    },
    {
     "name": "check_clock_offset",
     "kind": "function",
     "signature": "(report: PreflightReport, nodes: Iterable[Node]) -> None",
     "doc": "Every node's NTP offset must be below :data:`MAX_CLOCK_OFFSET_S`.\n\nDrifted clocks break CockroachDB and make cross-node timings meaningless.",
     "line": 203,
     "decorators": []
    },
    {
     "name": "check_leaseholder_placement",
     "kind": "function",
     "signature": "(report: PreflightReport, gateway: Node, database: str, expected_region: str, settle_timeout_s: float=0.0, poll_interval_s: float=10.0) -> None",
     "doc": "All of ``database``'s leaseholders must be in ``expected_region``.\n\nScoped to the workload database because system ranges are spread across\nregions by design. ``settle_timeout_s`` lets the placement recover after a\nprevious chaos run (the replication queue restores leases asynchronously);\nthe condition itself is never relaxed. A misplaced lease slows every\noperation while the cluster still reports healthy.",
     "line": 232,
     "decorators": []
    },
    {
     "name": "_read_leaseholder_placement",
     "kind": "function",
     "signature": "(gateway: Node, database: str, expected_region: str) -> tuple[bool, str, dict[str, Any]]",
     "doc": "One reading of ``database``'s leaseholder placement: ``(passed, detail, observed)``.",
     "line": 279,
     "decorators": []
    },
    {
     "name": "patroni_member_state",
     "kind": "function",
     "signature": "(node: Node, timeout_s: float=PATRONI_PRIMARY_TIMEOUT_S) -> dict[str, Any]",
     "doc": "Read one member's ``/patroni`` document (role, timeline, replication state), or ``{}``.",
     "line": 347,
     "decorators": []
    },
    {
     "name": "patroni_candidate_ready",
     "kind": "function",
     "signature": "(node: Node, timeout_s: float=PATRONI_PRIMARY_TIMEOUT_S, leader_timeline: int | None=None) -> tuple[bool, str]",
     "doc": "Would Patroni accept ``node`` as a switchover candidate? Returns ``(ready, reason)``.\n\nThree gates, all required:\n\n1. ``/replica`` answers 200 (up, in recovery, lag within bounds).\n2. ``/patroni`` shows ``replication_state: streaming``, on the leader's\n   timeline when known. A detached replica can pass gate 1 with no lag.\n3. ``/quorum`` answers 200: in ``synchronous_mode: quorum`` Patroni refuses\n   a candidate not yet in ``synchronous_standby_names``.\n\nThe reason explains a timeout (e.g. still re-cloning vs. process down).",
     "line": 370,
     "decorators": []
    },
    {
     "name": "resolve_patroni_primary",
     "kind": "function",
     "signature": "(topo: Topology, timeout_s: float=PATRONI_PRIMARY_TIMEOUT_S) -> Node",
     "doc": "Return the node whose Patroni ``/primary`` answers 200.\n\nRaises if none or more than one does (mid-failover or split-brain). This\nlive reading is the only source of truth for which node is primary.",
     "line": 439,
     "decorators": []
    },
    {
     "name": "check_patroni_primary_placement",
     "kind": "function",
     "signature": "(report: PreflightReport, topology: Topology, expected: Node | None=None, repair: bool=True, settle_timeout_s: float=0.0) -> Check",
     "doc": "The Patroni primary must be on ``expected`` (the gateway by default).\n\nKeeps the write path led from the same place on both engines. Patroni never\nfails back on its own, so if the primary is elsewhere and ``repair`` is set\nthis runs ``patronictl switchover``. ``settle_timeout_s`` waits for the\n*candidate* to become eligible first (after a partition it may need to\nrewind or re-clone); it never waits for the primary to move by itself.\nThe switchover is recorded in the report as a repair.",
     "line": 474,
     "decorators": []
    },
    {
     "name": "row_match_probe",
     "kind": "function",
     "signature": "(engine: str, *, gateway: Node, table: str, exec_node: Node | None=None, dsn: str | None=None, password: str='')",
     "doc": "The row-match probe for ``engine``.",
     "line": 861,
     "decorators": []
    },
    {
     "name": "quorum_floor_ms",
     "kind": "function",
     "signature": "(rtts_ms: dict[str, float], voters: int) -> float",
     "doc": "Round trip to the follower whose acknowledgement completes quorum.\n\nThe leader needs ``voters // 2`` follower acks, so the floor is the RTT to\nthe slowest of the fastest ``voters // 2`` followers. No committed write can\nbe faster; a lower write latency means the writes matched no rows.",
     "line": 883,
     "decorators": []
    },
    {
     "name": "check_write_latency_floor",
     "kind": "function",
     "signature": "(report: PreflightReport, observed_write_p50_ms: float, floor_ms: float, tolerance: float=0.9) -> bool",
     "doc": "Assert the observed write latency is physically achievable.\n\n``tolerance`` allows a small margin for jitter and ICMP-vs-Raft differences.",
     "line": 902,
     "decorators": []
    },
    {
     "name": "gateway_rtts",
     "kind": "function",
     "signature": "(network_csv, gateway_host: str) -> dict[str, float]",
     "doc": "Mean RTT from ``gateway_host`` to every other node in a Phase I matrix.",
     "line": 927,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/recorder.py",
   "module": "crdblab.core.recorder",
   "doc": "Measurement schemas, the run manifest, and a schema-enforcing CSV writer.\n\nTables are long (one row per interval and operation type), and no measurement\nis written without a manifest recording code revision, profile and topology.",
   "lines": 286,
   "constants": [
    {
     "name": "SCHEMA_VERSION",
     "value": "'2.1'",
     "doc": "",
     "line": 19
    },
    {
     "name": "COLUMNS",
     "value": "('ts_utc', 'elapsed_s', 'wall_offset_s', 'concurrency', 'repetition', 'op', 'tps', 'tps_cum', 'errors_cum', 'p50_ms', 'p95_ms', 'p99_ms', 'pmax_ms', 'gateway...",
     "doc": "Workload metrics, one row per (interval, op). Derived quantities are computed in the analysis layer, not stored.",
     "line": 23
    },
    {
     "name": "_REQUIRED",
     "value": "frozenset(COLUMNS)",
     "doc": "",
     "line": 44
    },
    {
     "name": "NETWORK_COLUMNS",
     "value": "('ts_utc', 'source', 'destination', 'source_region', 'destination_region', 'samples', 'loss_pct', 'rtt_min_ms', 'rtt_mean_ms', 'rtt_p50_ms', 'rtt_p95_ms', 'r...",
     "doc": "Phase I RTT matrix. Quantiles come from ping's per-packet lines, whose precision drops as RTT grows, so ``rtt_resolution_ms`` records the precision available.",
     "line": 48
    },
    {
     "name": "AUDIT_COLUMNS",
     "value": "('wall_offset_s', 'seq_id', 'outcome')",
     "doc": "RPO audit log: one row per attempted write, outcome ``ack``, ``ambiguous`` or ``refused``. Only an acknowledged write later found missing is data loss.",
     "line": 69
    },
    {
     "name": "PROBE_COLUMNS",
     "value": "('ts_utc', 'seq_id', 'dispatch_offset_s', 'complete_offset_s', 'duration_ms', 'outcome', 'worker', 'detail')",
     "doc": "RTO probe log: one row per canary write. The completion offset of a write that blocked through an outage marks the instant service resumed.",
     "line": 78
    },
    {
     "name": "PROBE_OUTCOMES",
     "value": "('ok', 'timeout', 'conn_error', 'refused')",
     "doc": "``ok`` means served; ``timeout`` is the outage signature; ``conn_error`` is a broken connection; ``refused`` is a probe bug, not downtime.",
     "line": 91
    },
    {
     "name": "HARDWARE_METRICS_COLUMNS",
     "value": "('ts_utc', 'wall_offset_s', 'node', 'host', 'cpu_busy_pct', 'cpu_seconds_idle_cum', 'cpu_seconds_total_cum', 'mem_total_bytes', 'mem_available_bytes', 'disk_...",
     "doc": "Per-node utilisation from node_exporter, one row per (node, poll). Rates sit beside their raw counters and are ``\"\"`` on a node's first poll (nothing to diff).",
     "line": 96
    }
   ],
   "classes": [
    {
     "name": "Manifest",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "Everything needed to reproduce or contextualise a single run.",
     "line": 147,
     "fields": [
      {
       "name": "run_id",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "phase",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "schema_version",
       "type": "str",
       "default": "SCHEMA_VERSION",
       "doc": ""
      },
      {
       "name": "engine",
       "type": "str",
       "default": "'cockroachdb'",
       "doc": "\"cockroachdb\" or \"postgresql\"; older runs default to cockroachdb."
      },
      {
       "name": "started_utc",
       "type": "str",
       "default": "field(default_factory=utcnow)",
       "doc": ""
      },
      {
       "name": "finished_utc",
       "type": "str | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "git_revision",
       "type": "str | None",
       "default": "field(default_factory=_git_revision)",
       "doc": ""
      },
      {
       "name": "profile",
       "type": "dict[str, Any]",
       "default": "field(default_factory=dict)",
       "doc": ""
      },
      {
       "name": "topology",
       "type": "list[dict[str, Any]]",
       "default": "field(default_factory=list)",
       "doc": ""
      },
      {
       "name": "clock_epoch_utc",
       "type": "str | None",
       "default": "None",
       "doc": "Wall-clock instant of the run's monotonic zero: the origin of ``wall_offset_s`` and every offset in ``events.json``."
      },
      {
       "name": "server_version",
       "type": "str | None",
       "default": "None",
       "doc": "Measured server version for either engine; ``cockroach_version`` is kept for older runs that only recorded that."
      },
      {
       "name": "cockroach_version",
       "type": "str | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "generator_command",
       "type": "str | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "ssh_options",
       "type": "list[str]",
       "default": "field(default_factory=list)",
       "doc": ""
      },
      {
       "name": "client_platform",
       "type": "str",
       "default": "field(default_factory=platform.platform)",
       "doc": ""
      },
      {
       "name": "notes",
       "type": "list[str]",
       "default": "field(default_factory=list)",
       "doc": ""
      },
      {
       "name": "generator_totals",
       "type": "dict[str, Any]",
       "default": "field(default_factory=dict)",
       "doc": ""
      },
      {
       "name": "validation",
       "type": "dict[str, Any]",
       "default": "field(default_factory=dict)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "note",
       "kind": "function",
       "signature": "(self, message: str) -> None",
       "doc": "",
       "line": 174,
       "decorators": []
      }
     ]
    },
    {
     "name": "RunDirectory",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "An immutable, self-describing output directory for one run.",
     "line": 178,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, root: Path, run_id: str) -> None",
       "doc": "",
       "line": 181,
       "decorators": []
      },
      {
       "name": "metrics_csv",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "",
       "line": 190,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "manifest_json",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "",
       "line": 194,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "events_json",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "",
       "line": 198,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "network_csv",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "Phase I round-trip matrix, written under :data:`NETWORK_COLUMNS`.",
       "line": 202,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "audit_csv",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "Phase III/IV audit attempt log, written under :data:`AUDIT_COLUMNS`.",
       "line": 207,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "probe_csv",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "High-frequency RTO probe attempts, written under :data:`PROBE_COLUMNS`.",
       "line": 212,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "probe_log",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "RTO probe connection-lifecycle log (JSONL).\n\nAppended and flushed as events happen, so a run killed mid-fault still\nleaves the outage edges on disk.",
       "line": 217,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "preflight_json",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "Pre-flight assertions and their observed values for this run.",
       "line": 226,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "hardware_metrics_csv",
       "kind": "function",
       "signature": "(self) -> Path",
       "doc": "Per-node utilisation under :data:`HARDWARE_METRICS_COLUMNS`, if collected.",
       "line": 231,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "write_preflight",
       "kind": "function",
       "signature": "(self, report: dict[str, Any]) -> None",
       "doc": "",
       "line": 235,
       "decorators": []
      },
      {
       "name": "raw",
       "kind": "function",
       "signature": "(self, name: str) -> Path",
       "doc": "",
       "line": 238,
       "decorators": []
      },
      {
       "name": "write_manifest",
       "kind": "function",
       "signature": "(self, manifest: Manifest) -> None",
       "doc": "",
       "line": 241,
       "decorators": []
      },
      {
       "name": "write_events",
       "kind": "function",
       "signature": "(self, events: dict[str, Any]) -> None",
       "doc": "",
       "line": 244,
       "decorators": []
      }
     ]
    },
    {
     "name": "MetricsWriter",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "Append-only CSV writer that rejects rows not matching ``columns`` exactly.\n\nA missing value must be written explicitly (e.g. ``\"\"``), never omitted.",
     "line": 248,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, path: Path, columns: tuple[str, ...]=COLUMNS) -> None",
       "doc": "",
       "line": 254,
       "decorators": []
      },
      {
       "name": "write",
       "kind": "function",
       "signature": "(self, row: dict[str, Any]) -> None",
       "doc": "",
       "line": 263,
       "decorators": []
      },
      {
       "name": "write_many",
       "kind": "function",
       "signature": "(self, rows: Iterable[dict[str, Any]]) -> None",
       "doc": "",
       "line": 275,
       "decorators": []
      },
      {
       "name": "close",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 279,
       "decorators": []
      },
      {
       "name": "__enter__",
       "kind": "function",
       "signature": "(self) -> MetricsWriter",
       "doc": "",
       "line": 282,
       "decorators": []
      },
      {
       "name": "__exit__",
       "kind": "function",
       "signature": "(self, *exc) -> None",
       "doc": "",
       "line": 285,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "utcnow",
     "kind": "function",
     "signature": "() -> str",
     "doc": "",
     "line": 120,
     "decorators": []
    },
    {
     "name": "utcnow_us",
     "kind": "function",
     "signature": "() -> str",
     "doc": "UTC timestamp at microsecond resolution, for probe events.",
     "line": 124,
     "decorators": []
    },
    {
     "name": "new_run_id",
     "kind": "function",
     "signature": "(phase: str) -> str",
     "doc": "",
     "line": 131,
     "decorators": []
    },
    {
     "name": "_git_revision",
     "kind": "function",
     "signature": "() -> str | None",
     "doc": "",
     "line": 136,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/remote_probe.py",
   "module": "crdblab.core.remote_probe",
   "doc": "Run the RTO probe on the client node and read its observations back.\n\nThe probe runs on ``crdb-client-1`` so each canary write pays only the\nclient-to-cluster round trip, and so a workstation network hiccup cannot be\nmistaken for a database outage. The same :mod:`crdblab.core.rto_probe` code is\ncopied over and run with ``python3 -m``.\n\nThe agent reports offsets from its own epoch; they are rebased onto the\nharness clock by the difference between the two epochs' UTC stamps. That is\nvalid because pre-flight asserts the client node's NTP offset first.",
   "lines": 330,
   "constants": [
    {
     "name": "AGENT_ROOT",
     "value": "'/tmp/crdblab-probe-agent'",
     "doc": "Agent install location on the client node, rewritten from this checkout every run.",
     "line": 37
    },
    {
     "name": "AGENT_FILES",
     "value": "('crdblab/__init__.py', 'crdblab/core/__init__.py', 'crdblab/core/recorder.py', 'crdblab/core/rto_probe.py')",
     "doc": "The only files the agent needs (stdlib plus ``psycopg``).",
     "line": 40
    }
   ],
   "classes": [
    {
     "name": "RemoteProbeError",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "The agent could not be installed or started on the client node.",
     "line": 48,
     "fields": [],
     "methods": []
    },
    {
     "name": "RemoteRtoProbe",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "The probe running on ``node``, with the same interface as ``RtoProbe``.\n\nNever raises into the run it observes; fatal problems land in :attr:`error`.",
     "line": 103,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, node: Node, dsn: str, *, package_root: Path, duration_s: float, table: str=DEFAULT_TABLE, interval_s: float=DEFAULT_INTERVAL_S, workers: int=DEFAULT_WORKERS, statement_timeout_ms: int=DEFAULT_STATEMENT_TIMEOUT_MS, connect_timeout_s: float=DEFAULT_CONNECT_TIMEOUT_S, epoch_monotonic: float, epoch_utc: str, log_path: Path | None=None) -> None",
       "doc": "",
       "line": 109,
       "decorators": []
      },
      {
       "name": "_remote_command",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 157,
       "decorators": []
      },
      {
       "name": "start",
       "kind": "function",
       "signature": "(self) -> RemoteRtoProbe",
       "doc": "",
       "line": 168,
       "decorators": []
      },
      {
       "name": "_read_stderr",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 188,
       "decorators": []
      },
      {
       "name": "_read_stdout",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 193,
       "decorators": []
      },
      {
       "name": "_on_start",
       "kind": "function",
       "signature": "(self, payload: dict[str, Any]) -> None",
       "doc": "",
       "line": 216,
       "decorators": []
      },
      {
       "name": "_on_stop",
       "kind": "function",
       "signature": "(self, payload: dict[str, Any]) -> None",
       "doc": "",
       "line": 231,
       "decorators": []
      },
      {
       "name": "_on_attempt",
       "kind": "function",
       "signature": "(self, row: dict[str, Any]) -> None",
       "doc": "",
       "line": 243,
       "decorators": []
      },
      {
       "name": "stop",
       "kind": "function",
       "signature": "(self, timeout_s: float=20.0) -> None",
       "doc": "",
       "line": 264,
       "decorators": []
      },
      {
       "name": "__enter__",
       "kind": "function",
       "signature": "(self) -> RemoteRtoProbe",
       "doc": "",
       "line": 283,
       "decorators": []
      },
      {
       "name": "__exit__",
       "kind": "function",
       "signature": "(self, *exc) -> None",
       "doc": "",
       "line": 290,
       "decorators": []
      },
      {
       "name": "rows",
       "kind": "function",
       "signature": "(self)",
       "doc": "",
       "line": 296,
       "decorators": []
      },
      {
       "name": "summary",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "Summary re-derived from the attempts; the agent's own is kept under\n``agent_summary`` for comparison.",
       "line": 299,
       "decorators": []
      },
      {
       "name": "rto",
       "kind": "function",
       "signature": "(self, fault_offset_s: float, observation_end_s: float | None=None) -> dict[str, Any]",
       "doc": "",
       "line": 327,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_parse_utc",
     "kind": "function",
     "signature": "(stamp: str) -> datetime",
     "doc": "",
     "line": 52,
     "decorators": []
    },
    {
     "name": "check_agent_prerequisites",
     "kind": "function",
     "signature": "(node: Node) -> tuple[bool, str]",
     "doc": "Check the client node can run the probe (python3 with ``psycopg``).\n\nChecked up front: an agent that cannot import ``psycopg`` would otherwise\nlook like a total outage from the first sample.",
     "line": 56,
     "decorators": []
    },
    {
     "name": "install_agent",
     "kind": "function",
     "signature": "(node: Node, package_root: Path) -> None",
     "doc": "Copy this checkout's probe code onto the client node, every run, so the\nagent matches the git revision the manifest records.",
     "line": 76,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/rto_probe.py",
   "module": "crdblab.core.rto_probe",
   "doc": "High-frequency availability probe: how long could the database not serve a write?\n\nThe RPO audit writer issues one write at a time, so its resolution is bounded\nby the quorum write cost (~70 ms). This probe keeps several canary writes in\nflight on separate connections, so the gap between observations is roughly the\nwrite cost divided by the worker count (~21-29 ms from the client node).\n\nDesign points:\n\n* It runs on its own threads, connections and table, and cannot fail the\n  workload. Its extra write rate is reported as ``achieved_rate_per_s``.\n* ``resolution_s`` is measured from the observed gaps, not taken from the\n  configured 2 ms dispatch interval.\n* A write that blocks through a failover and then commits is the measurement,\n  so ``statement_timeout`` is generous (5 s).\n* Every attempt uses a fresh sequence number and is never retried.",
   "lines": 982,
   "constants": [
    {
     "name": "DEFAULT_INTERVAL_S",
     "value": "0.002",
     "doc": "Dispatch cadence. The achieved rate is lower and reported separately.",
     "line": 34
    },
    {
     "name": "DEFAULT_WORKERS",
     "value": "8",
     "doc": "Concurrent in-flight writes. More workers give finer resolution but add load and connections; check ``resolution_s`` per run.",
     "line": 38
    },
    {
     "name": "DEFAULT_STATEMENT_TIMEOUT_MS",
     "value": "5000",
     "doc": "Server-side budget per canary write: a hang detector, not a latency budget.",
     "line": 41
    },
    {
     "name": "DEFAULT_CONNECT_TIMEOUT_S",
     "value": "2.0",
     "doc": "A connection that cannot be made is itself an observation, so keep this short.",
     "line": 44
    },
    {
     "name": "DEFAULT_TABLE",
     "value": "'rto_canary'",
     "doc": "Dedicated table, so its outage cannot be confused with the workload's or audit's.",
     "line": 47
    },
    {
     "name": "CREATE_TABLE_SQL",
     "value": "'CREATE TABLE IF NOT EXISTS {table} (seq_id INT8 PRIMARY KEY, written_at TIMESTAMPTZ NOT NULL DEFAULT now())'",
     "doc": "",
     "line": 49
    },
    {
     "name": "COVERAGE_SLACK_PERIODS",
     "value": "20.0",
     "doc": "How far (in sampling periods) the last observation may fall short of the run's end before the probe counts as having stopped observing.",
     "line": 599
    },
    {
     "name": "AGENT_RESULT_KEY",
     "value": "'__agent__'",
     "doc": "Key marking the agent's start/stop lines (vs. attempt rows).",
     "line": 894
    }
   ],
   "classes": [
    {
     "name": "ProbeAttempt",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "One canary write as the client saw it.\n\nOffsets are seconds from the caller-supplied epoch, shared with\n``events.json`` and ``metrics.csv``'s ``wall_offset_s``.",
     "line": 57,
     "fields": [
      {
       "name": "seq_id",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "dispatch_offset_s",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "complete_offset_s",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "outcome",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "worker",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "detail",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "ts_utc",
       "type": "str",
       "default": "''",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "duration_ms",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "",
       "line": 73,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "served",
       "kind": "function",
       "signature": "(self) -> bool",
       "doc": "",
       "line": 77,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "to_row",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "A row under :data:`crdblab.core.recorder.PROBE_COLUMNS`.",
       "line": 80,
       "decorators": []
      }
     ]
    },
    {
     "name": "_EventLog",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "Append-only JSON-lines log of connection lifecycle events.\n\nFlushed on every write so it survives a run killed mid-fault. Only the\nedges are logged (failures, connects, disconnects), not every success.",
     "line": 113,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, path: Path | None) -> None",
       "doc": "",
       "line": 120,
       "decorators": []
      },
      {
       "name": "open",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 125,
       "decorators": []
      },
      {
       "name": "close",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 129,
       "decorators": []
      },
      {
       "name": "write",
       "kind": "function",
       "signature": "(self, event: str, offset_s: float, **fields: Any) -> None",
       "doc": "",
       "line": 135,
       "decorators": []
      }
     ]
    },
    {
     "name": "RtoProbe",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "A pool of canary writers on a background path, used as a context manager.\n\nWorker exceptions become classified observations; anything that stops the\nprobe entirely is stored in :attr:`error` instead of raised.",
     "line": 151,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, dsn: str, *, table: str=DEFAULT_TABLE, interval_s: float=DEFAULT_INTERVAL_S, workers: int=DEFAULT_WORKERS, statement_timeout_ms: int=DEFAULT_STATEMENT_TIMEOUT_MS, connect_timeout_s: float=DEFAULT_CONNECT_TIMEOUT_S, log_path: Path | None=None, epoch_monotonic: float | None=None, emit: TextIO | None=None) -> None",
       "doc": "",
       "line": 158,
       "decorators": []
      },
      {
       "name": "offset",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "",
       "line": 215,
       "decorators": []
      },
      {
       "name": "_next_seq",
       "kind": "function",
       "signature": "(self) -> int",
       "doc": "",
       "line": 218,
       "decorators": []
      },
      {
       "name": "_record",
       "kind": "function",
       "signature": "(self, attempt: ProbeAttempt) -> None",
       "doc": "",
       "line": 223,
       "decorators": []
      },
      {
       "name": "_note_latency",
       "kind": "function",
       "signature": "(self, seconds: float) -> None",
       "doc": "Fold a served write's latency into the dispatch-spacing estimate.\n\nFailed writes are excluded so fast refusals cannot make the probe fire\nharder at an unhealthy cluster.",
       "line": 233,
       "decorators": []
      },
      {
       "name": "_connect",
       "kind": "function",
       "signature": "(self, worker: int)",
       "doc": "",
       "line": 244,
       "decorators": []
      },
      {
       "name": "_worker",
       "kind": "function",
       "signature": "(self, worker: int) -> None",
       "doc": "",
       "line": 255,
       "decorators": []
      },
      {
       "name": "_spacing",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "Minimum interval between dispatches: median write latency / workers.\n\nWithout it the workers phase-lock: they all finish together, get\nre-dispatched together, and return in bursts with long blind gaps between.\nUntil latency is known, ``interval_s`` alone applies.",
       "line": 320,
       "decorators": []
      },
      {
       "name": "_dispatcher",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "Tick on absolute deadlines (``epoch + n * interval``) so cadence cannot drift.\n\nA tick dispatches only if a worker is free and :meth:`_spacing` allows it.",
       "line": 332,
       "decorators": []
      },
      {
       "name": "start",
       "kind": "function",
       "signature": "(self) -> RtoProbe",
       "doc": "",
       "line": 365,
       "decorators": []
      },
      {
       "name": "stop",
       "kind": "function",
       "signature": "(self, timeout_s: float=15.0) -> None",
       "doc": "",
       "line": 391,
       "decorators": []
      },
      {
       "name": "__enter__",
       "kind": "function",
       "signature": "(self) -> RtoProbe",
       "doc": "",
       "line": 400,
       "decorators": []
      },
      {
       "name": "__exit__",
       "kind": "function",
       "signature": "(self, *exc) -> None",
       "doc": "",
       "line": 407,
       "decorators": []
      },
      {
       "name": "rows",
       "kind": "function",
       "signature": "(self) -> Iterable[dict[str, Any]]",
       "doc": "Attempts as rows under :data:`PROBE_COLUMNS`, in completion order.",
       "line": 413,
       "decorators": []
      },
      {
       "name": "summary",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 417,
       "decorators": []
      },
      {
       "name": "rto",
       "kind": "function",
       "signature": "(self, fault_offset_s: float, observation_end_s: float | None=None) -> dict[str, Any]",
       "doc": "",
       "line": 427,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "classify",
     "kind": "function",
     "signature": "(exc: BaseException) -> tuple[str, str]",
     "doc": "Map a driver exception to a :data:`PROBE_OUTCOMES` member and a detail.\n\n``timeout`` (statement accepted, never answered) is the outage signature and\nis kept apart from ``conn_error`` (connection lost or refused).",
     "line": 94,
     "decorators": []
    },
    {
     "name": "_when",
     "kind": "function",
     "signature": "(offset_s: float) -> str",
     "doc": "Phrase an offset from the fault; the last write before an outage can\nslightly predate the fault.",
     "line": 437,
     "decorators": []
    },
    {
     "name": "_median",
     "kind": "function",
     "signature": "(values: list[float]) -> float | None",
     "doc": "",
     "line": 447,
     "decorators": []
    },
    {
     "name": "_quantile",
     "kind": "function",
     "signature": "(values: list[float], q: float) -> float | None",
     "doc": "Nearest-rank quantile, so every value returned was actually observed.",
     "line": 457,
     "decorators": []
    },
    {
     "name": "resolution_of",
     "kind": "function",
     "signature": "(gaps: list[float]) -> float | None",
     "doc": "The interval an RTO may be quoted to: the 95th percentile of the gaps.\n\nThe median is misleading for a bursty, bimodal gap distribution, and the\nmaximum is a single scheduling accident. ``summarise`` reports both anyway.",
     "line": 466,
     "decorators": []
    },
    {
     "name": "summarise",
     "kind": "function",
     "signature": "(attempts: list[ProbeAttempt], *, ticks: int=0, saturated: int=0, spaced_out: int=0, interval_s: float=DEFAULT_INTERVAL_S, workers: int=DEFAULT_WORKERS) -> dict[str, Any]",
     "doc": "What the probe achieved, as distinct from what it was configured to do.",
     "line": 475,
     "decorators": []
    },
    {
     "name": "served_attempts",
     "kind": "function",
     "signature": "(attempts: list[ProbeAttempt]) -> list[ProbeAttempt]",
     "doc": "",
     "line": 525,
     "decorators": []
    },
    {
     "name": "outage_windows",
     "kind": "function",
     "signature": "(attempts: list[ProbeAttempt], min_gap_s: float=0.0) -> list[dict[str, float]]",
     "doc": "Intervals between consecutive served writes, longest first.\n\nEach window carries both edges: the true recovery lies somewhere inside it.",
     "line": 529,
     "decorators": []
    },
    {
     "name": "tail_attribution",
     "kind": "function",
     "signature": "(pre_gaps: list[float], post_gaps: list[float]) -> dict[str, Any]",
     "doc": "Is the post-fault gap tail heavier than the pre-fault one, or just longer?\n\nThe post-fault window is usually longer, so its maximum gap is larger by\nchance alone. This compares *rates* of gaps over the healthy 95th percentile\ninstead, and returns the evidence as well as the verdict.",
     "line": 555,
     "decorators": []
    },
    {
     "name": "measure_rto",
     "kind": "function",
     "signature": "(attempts: list[ProbeAttempt], fault_offset_s: float, observation_end_s: float | None=None) -> dict[str, Any]",
     "doc": "How long the database could not serve a write after the fault.\n\nA cluster keeps serving for a few seconds before it notices a lost member,\nso \"fault to next served write\" is wrong. Instead the outage is the largest\npost-fault gap in served writes that exceeds the noise floor (the longest\ngap that closed before the fault, plus one sampling period).\n\nKey results:\n\n``rto_s``\n    Fault to service restored, or ``None`` when no gap cleared the floor.\n``outage``\n    The gap itself, with both edges.\n``detection_lag_s``\n    Fault to the first blocked or failed attempt, reported separately.\n``next_write_after_fault_s``\n    Fault to the next served write, comparable with the audit log's figure.\n``observed_outage_s``\n    The gap between two probe observations, where link delay cancels out.\n\nAn outage still open when the probe stopped is ``truncated``. If\n``observation_end_s`` is given and the probe's last attempt falls well\nshort of it, ``coverage_truncated`` is set: the probe stopped observing\n(e.g. blocked on a black-holed socket), so absence of an outage means nothing.",
     "line": 602,
     "decorators": []
    },
    {
     "name": "attempts_from_rows",
     "kind": "function",
     "signature": "(rows: Iterable[dict[str, Any]]) -> list[ProbeAttempt]",
     "doc": "Rebuild attempts from a recorded ``rto_probe.csv`` for re-analysis.",
     "line": 872,
     "decorators": []
    },
    {
     "name": "_agent_main",
     "kind": "function",
     "signature": "(argv: list[str] | None=None) -> int",
     "doc": "",
     "line": 897,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/ssh.py",
   "module": "crdblab.core.ssh",
   "doc": "Centralised SSH invocation.\n\nEvery remote command routes through here. Host-key checking is disabled because\nthe testbed is destroyed and rebuilt, and providers reuse addresses. Output is\nstreamed line by line (``bufsize=1``) so samples are parsed as they arrive.",
   "lines": 102,
   "constants": [
    {
     "name": "SSH_OPTIONS",
     "value": "('-q', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountM...",
     "doc": "Options for every invocation; recorded in each run manifest.",
     "line": 17
    },
    {
     "name": "SUDO",
     "value": "'sudo -n'",
     "doc": "Prefix for privileged remote commands (several nodes log in as ``ubuntu``). ``-n`` fails fast instead of hanging on a password prompt.",
     "line": 28
    }
   ],
   "classes": [
    {
     "name": "RemoteResult",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 39,
     "fields": [
      {
       "name": "returncode",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "stdout",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "stderr",
       "type": "str",
       "default": "",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "StreamingRemote",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "Line-wise streaming execution of a long-running remote command.\n\nLines are yielded as they arrive and, if ``tee`` is given, written to it\nfirst, so every run keeps the raw generator output.",
     "line": 57,
     "fields": [
      {
       "name": "node",
       "type": "Node",
       "default": "",
       "doc": ""
      },
      {
       "name": "remote",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "tee",
       "type": "object | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "_proc",
       "type": "subprocess.Popen | None",
       "default": "field(default=None, init=False, repr=False)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "__enter__",
       "kind": "function",
       "signature": "(self) -> StreamingRemote",
       "doc": "",
       "line": 69,
       "decorators": []
      },
      {
       "name": "__iter__",
       "kind": "function",
       "signature": "(self) -> Iterator[str]",
       "doc": "",
       "line": 79,
       "decorators": []
      },
      {
       "name": "__exit__",
       "kind": "function",
       "signature": "(self, *exc) -> None",
       "doc": "",
       "line": 87,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "build_command",
     "kind": "function",
     "signature": "(node: Node, remote: str | None=None) -> list[str]",
     "doc": "",
     "line": 31,
     "decorators": []
    },
    {
     "name": "run",
     "kind": "function",
     "signature": "(node: Node, remote: str, timeout: float | None=60.0) -> RemoteResult",
     "doc": "Execute a command and wait for it. For short, non-streaming commands.",
     "line": 45,
     "decorators": []
    },
    {
     "name": "force_tty",
     "kind": "function",
     "signature": "(remote: str) -> str",
     "doc": "Wrap a command so the generator believes it is writing to a terminal.\n\n``cockroach workload run`` prints per-interval lines only to a terminal, so\nthe command is wrapped in ``script`` to allocate a pseudo-terminal.",
     "line": 95,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/core/workload.py",
   "module": "crdblab.core.workload",
   "doc": "Header-driven parser for ``cockroach workload run`` output.\n\nColumn positions are bound from the generator's own header line, never guessed\nfrom field counts. Each periodic interval emits one line per operation type,\nwith an unheaded trailing op label; the cumulative summary block has a\ndifferent header and is kept apart from per-interval samples. Aggregation\n(sum throughput across ops, never pool latency) happens in :func:`aggregate_tick`.",
   "lines": 288,
   "constants": [
    {
     "name": "_HEADER_RE",
     "value": "re.compile('^_+elapsed')",
     "doc": "",
     "line": 18
    },
    {
     "name": "_UNDERSCORE_RUN_RE",
     "value": "re.compile('_+')",
     "doc": "",
     "line": 19
    },
    {
     "name": "_ELAPSED_TOKEN_RE",
     "value": "re.compile('^\\\\d+(?:\\\\.\\\\d+)?s$')",
     "doc": "",
     "line": 20
    },
    {
     "name": "_NUMERIC_TOKEN_RE",
     "value": "re.compile('^[+-]?\\\\d+(?:\\\\.\\\\d+)?(?:[eE][+-]?\\\\d+)?$')",
     "doc": "",
     "line": 21
    },
    {
     "name": "_COLUMN_ALIASES",
     "value": "{'elapsed': 'elapsed_s', 'errors': 'errors_cum', 'ops/sec(inst)': 'tps', 'ops/sec(cum)': 'tps_cum', 'ops(total)': 'ops_total', 'avg(ms)': 'avg_ms', 'p50(ms)'...",
     "doc": "Column names as printed by cockroach, mapped to canonical field names.",
     "line": 24
    },
    {
     "name": "PERIODIC",
     "value": "'periodic'",
     "doc": "",
     "line": 38
    },
    {
     "name": "SUMMARY",
     "value": "'summary'",
     "doc": "",
     "line": 39
    },
    {
     "name": "_SUMMARY_MARKERS",
     "value": "frozenset({'ops_total', 'avg_ms'})",
     "doc": "Columns that identify a cumulative summary block rather than a periodic one.",
     "line": 42
    },
    {
     "name": "_METRIC_COLUMNS",
     "value": "frozenset(_COLUMN_ALIASES.values())",
     "doc": "Measured quantities. A trailing header token outside this set names the operation-type column, which periodic and summary headers label differently.",
     "line": 46
    }
   ],
   "classes": [
    {
     "name": "WorkloadParseError",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "Raised when output cannot be interpreted without positional guessing.",
     "line": 49,
     "fields": [],
     "methods": []
    },
    {
     "name": "Sample",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "One parsed line of generator output.\n\nOnly :data:`PERIODIC` samples are measurements; :data:`SUMMARY` samples are\nthe generator's cumulative totals, kept as a cross-check.",
     "line": 54,
     "fields": [
      {
       "name": "kind",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "elapsed_s",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "op",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "errors_cum",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "values",
       "type": "dict[str, float]",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "tps",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "",
       "line": 68,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "latency_ms",
       "kind": "function",
       "signature": "(self, quantile: str) -> float",
       "doc": "",
       "line": 71,
       "decorators": []
      }
     ]
    },
    {
     "name": "_Header",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 76,
     "fields": [
      {
       "name": "columns",
       "type": "list[str]",
       "default": "",
       "doc": ""
      },
      {
       "name": "kind",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "op_column",
       "type": "str | None",
       "default": "None",
       "doc": "Name of the trailing header token that labels the operation type, when the block declares one. ``None`` when the label is emitted unheaded."
      }
     ],
     "methods": []
    },
    {
     "name": "WorkloadParser",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "Incremental, header-bound parser.\n\nFeed it lines in arrival order. It yields a :class:`Sample` for every data\nline and ``None`` for headers, blank lines and generator chatter. State is\nreset whenever a new header appears, so a run containing several blocks\n(init, run, summary) is handled without special-casing.",
     "line": 85,
     "fields": [
      {
       "name": "strict",
       "type": "bool",
       "default": "True",
       "doc": ""
      },
      {
       "name": "_header",
       "type": "_Header | None",
       "default": "field(default=None, init=False, repr=False)",
       "doc": ""
      },
      {
       "name": "unparsed",
       "type": "list[str]",
       "default": "field(default_factory=list, init=False, repr=False)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "_parse_header",
       "kind": "function",
       "signature": "(line: str) -> _Header",
       "doc": "",
       "line": 100,
       "decorators": [
        "staticmethod"
       ]
      },
      {
       "name": "feed",
       "kind": "function",
       "signature": "(self, line: str) -> Sample | None",
       "doc": "",
       "line": 113,
       "decorators": []
      },
      {
       "name": "_bind",
       "kind": "function",
       "signature": "(self, fields: list[str], header: _Header) -> Sample",
       "doc": "",
       "line": 139,
       "decorators": []
      },
      {
       "name": "parse_stream",
       "kind": "function",
       "signature": "(self, lines: Iterable[str]) -> Iterator[Sample]",
       "doc": "",
       "line": 187,
       "decorators": []
      }
     ]
    },
    {
     "name": "Tick",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "All operation types observed at one elapsed offset.\n\n``total_tps`` sums throughput across ops. Latency stays per op: averaging a\nread p99 and a write p99 is not a quantile of anything.",
     "line": 195,
     "fields": [
      {
       "name": "elapsed_s",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "total_tps",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "errors_cum",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "by_op",
       "type": "dict[str, Sample]",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "latency_ms",
       "kind": "function",
       "signature": "(self, op: str, quantile: str) -> float",
       "doc": "",
       "line": 207,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "aggregate_tick",
     "kind": "function",
     "signature": "(samples: Iterable[Sample]) -> Tick",
     "doc": "Fold the samples sharing one elapsed offset into a single tick.",
     "line": 212,
     "decorators": []
    },
    {
     "name": "_grouped_pairs",
     "kind": "function",
     "signature": "(arrivals: Iterable[tuple[float | None, Sample]]) -> Iterator[list[tuple[float | None, Sample]]]",
     "doc": "Lazily split a periodic sample stream at each change of elapsed offset.\n\nSummary blocks are dropped. Shared by :func:`group_ticks` and\n:func:`group_timed_ticks` so the interval rule lives in one place.",
     "line": 247,
     "decorators": []
    },
    {
     "name": "group_ticks",
     "kind": "function",
     "signature": "(samples: Iterable[Sample]) -> Iterator[Tick]",
     "doc": "Group a periodic sample stream into ticks, discarding summary blocks.",
     "line": 269,
     "decorators": []
    },
    {
     "name": "group_timed_ticks",
     "kind": "function",
     "signature": "(arrivals: Iterable[tuple[float, Sample]]) -> Iterator[tuple[float, Tick]]",
     "doc": "As :func:`group_ticks`, but pairing each tick with when it was observed.\n\nEach sample comes with the harness's monotonic clock reading when its line\nwas read; a tick is stamped with its first line's arrival. The generator's\n``elapsed`` clock starts ~5 s later (SSH and process startup), so both are\nrecorded and the offset between them is observed rather than assumed.",
     "line": 275,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/insights/__init__.py",
   "module": "crdblab.insights",
   "doc": "The chart catalogue: 31 charts in five groups, drawn from every run on disk.\n\n``crdblab insights`` (or ``./generate_insights.sh``) renders it into\n``insights/<stamp>_<profile|all>/``, with ``insights.md``, a self-contained\n``dashboard.html``, and ``summary.json``/``summary.csv``/``chart_status.csv``\nbeside the charts.\n\nGroups: A benchmark and saturation, B hardware utilisation, C resilience,\nD engine comparison, E network and provenance.",
   "lines": 14,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/insights/_base.py",
   "module": "crdblab.insights._base",
   "doc": "The chart registry and the conventions every chart shares.\n\nA chart is a function ``(ctx) -> Drawn`` registered with :func:`chart`. It\neither draws and returns the numbers behind the picture, or raises\n:class:`Skip` with a reason, which is reported beside the charts that drew.\nStats come from the analysis layer and are written to ``summary.json``/``.csv``.",
   "lines": 122,
   "constants": [
    {
     "name": "GROUPS",
     "value": "{'A': 'Benchmark and saturation', 'B': 'Hardware utilisation', 'C': 'Resilience', 'D': 'Engine comparison', 'E': 'Network and provenance'}",
     "doc": "Group letter -> heading, in report order.",
     "line": 24
    },
    {
     "name": "LABEL",
     "value": "{'cockroachdb': 'CockroachDB', 'postgresql': 'PostgreSQL/Patroni'}",
     "doc": "Display names for engines; lower-case ids stay in stat keys and filenames.",
     "line": 33
    },
    {
     "name": "COLOR",
     "value": "{'cockroachdb': SERIES[0], 'postgresql': SERIES[1]}",
     "doc": "",
     "line": 34
    },
    {
     "name": "MARKER",
     "value": "{'cockroachdb': 'o', 'postgresql': 's'}",
     "doc": "",
     "line": 35
    },
    {
     "name": "DASH",
     "value": "{'cockroachdb': '-', 'postgresql': '--'}",
     "doc": "",
     "line": 36
    },
    {
     "name": "REGISTRY",
     "value": "[]",
     "doc": "",
     "line": 82
    }
   ],
   "classes": [
    {
     "name": "Skip",
     "kind": "class",
     "bases": [
      "Exception"
     ],
     "decorators": [],
     "doc": "A chart's inputs are absent; the message says which, in a sentence.",
     "line": 39,
     "fields": [],
     "methods": []
    },
    {
     "name": "Context",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "What a chart may read: the gated inventory, and where to write.",
     "line": 44,
     "fields": [
      {
       "name": "inventory",
       "type": "Inventory",
       "default": "",
       "doc": ""
      },
      {
       "name": "out_dir",
       "type": "Path",
       "default": "",
       "doc": ""
      },
      {
       "name": "cache",
       "type": "dict[str, Any]",
       "default": "field(default_factory=dict)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "memo",
       "kind": "function",
       "signature": "(self, key: str, compute: Callable[[], Any]) -> Any",
       "doc": "Compute an expensive shared input once per render (e.g. the comparison).",
       "line": 51,
       "decorators": []
      }
     ]
    },
    {
     "name": "Drawn",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 59,
     "fields": [
      {
       "name": "stats",
       "type": "dict[str, Any]",
       "default": "",
       "doc": ""
      },
      {
       "name": "files",
       "type": "list[str]",
       "default": "",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "Chart",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "",
     "line": 65,
     "fields": [
      {
       "name": "id",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "title",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "caption",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "fn",
       "type": "Callable[[Context], Drawn]",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "group",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 72,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "stem",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "``a1_throughput_latency_curve``: the id plus the title, filename-safe.",
       "line": 76,
       "decorators": [
        "property"
       ]
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "chart",
     "kind": "function",
     "signature": "(chart_id: str, title: str, caption: str)",
     "doc": "Register a chart function under a stable id, title and caption.",
     "line": 85,
     "decorators": []
    },
    {
     "name": "new_figure",
     "kind": "function",
     "signature": "(*args, **kwargs)",
     "doc": "``plt.subplots`` under the house style.",
     "line": 95,
     "decorators": []
    },
    {
     "name": "save",
     "kind": "function",
     "signature": "(ctx: Context, chart_id: str, fig, axes, runs: Sequence[Any]) -> list[str]",
     "doc": "Write one chart as PNG + SVG, named and footed with its provenance.\n\n``runs`` is every run the chart drew from, in the order it drew them. With no\nruns at all -- a chart about the inventory itself -- the footer says so\nrather than naming nothing.",
     "line": 101,
     "decorators": []
    },
    {
     "name": "need",
     "kind": "function",
     "signature": "(value: Any, reason: str) -> Any",
     "doc": "Return ``value``, or skip the chart with ``reason`` if it is missing.",
     "line": 118,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/insights/charts_a.py",
   "module": "crdblab.insights.charts_a",
   "doc": "Group A: benchmark and saturation, from each engine's newest Phase II sweep.\n\nTier statistics come from :mod:`crdblab.analysis.steady_state`; nothing is\nre-aggregated here.",
   "lines": 306,
   "constants": [
    {
     "name": "BUDGETS_MS",
     "value": "(10, 25, 50, 100, 200, 400, 800)",
     "doc": "p99 budgets for A6, in ms: from the read path (a few ms) to well past the write quorum floor (~70 ms).",
     "line": 29
    },
    {
     "name": "_QUANTILE_DASHES",
     "value": "{'p50_ms': '-', 'p95_ms': '--', 'p99_ms': '-.', 'pmax_ms': ':'}",
     "doc": "",
     "line": 31
    },
    {
     "name": "SETTLED_CV",
     "value": "0.25",
     "doc": "Within-tier coefficient of variation above which a tier was still moving (same threshold as resilience analysis).",
     "line": 165
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "_bench",
     "kind": "function",
     "signature": "(ctx: Context) -> dict",
     "doc": "",
     "line": 34,
     "decorators": []
    },
    {
     "name": "_peak",
     "kind": "function",
     "signature": "(tiers: pd.DataFrame) -> pd.Series",
     "doc": "",
     "line": 41,
     "decorators": []
    },
    {
     "name": "a1_throughput_latency",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 53,
     "decorators": [
      "chart('A1', 'Throughput-latency curve', 'Each point is one concurrency tier. The curve bends upward at the knee, past\\n    which offered load buys queueing rather than throughput. Points are ordered by\\n    concurrency, not by throughput, because past saturation the curve genuinely\\n    bends backwards.')"
     ]
    },
    {
     "name": "a2_percentile_fan",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 93,
     "decorators": [
      "chart('A2', 'Latency percentile fan', 'p50, p95, p99 and max for each operation type, per engine, on a log axis. A fan\\n    that opens with concurrency is queueing; one that stays parallel is a shifted\\n    floor. Operation types are never pooled.')"
     ]
    },
    {
     "name": "a3_slot_occupancy",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 134,
     "decorators": [
      "chart('A3', 'Concurrency slot occupancy', \"Little's law applied per operation type: an operation occupies throughput x\\n    latency of the client's fixed concurrency budget, and both factors are measured\\n    per operation rather than assumed from the configured mix. Where reads grow\\n    expensive they crowd out writes for the same slots, which is a different\\n    statement from either one simply being slow.\")"
     ]
    },
    {
     "name": "a4_stability",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 175,
     "decorators": [
      "chart('A4', 'Steady-state stability', 'Within-tier coefficient of variation of throughput. A tier above the line was\\n    still moving while it was being recorded, and its mean is a average over a\\n    transient rather than a steady state.')"
     ]
    },
    {
     "name": "a5_errors",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 208,
     "decorators": [
      "chart('A5', 'Error rate against load', 'Errors per tier. The bench sweep runs without --tolerate-errors, so a non-zero\\n    count here would mean the generator survived something it was not configured to\\n    absorb; zero is the expected result and is what makes the throughput figures\\n    quotable.')"
     ]
    },
    {
     "name": "a6_latency_budget",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 241,
     "decorators": [
      "chart('A6', 'Throughput under a latency budget', 'The highest measured throughput whose p99 stayed inside each budget. A zero bar\\n    means no tier met that budget at all. This restates the same tiers as A1 in the\\n    form a service owner buys: peak throughput at an unbounded tail is not\\n    deliverable capacity.')"
     ]
    },
    {
     "name": "a7_throughput_by_concurrency",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 275,
     "decorators": [
      "chart('A7', 'Throughput by concurrency', 'Mean throughput per tier against the concurrency that produced it. Flattening\\n    or falling back past some concurrency is saturation; A1 shows what that\\n    saturation costs in latency.')"
     ]
    }
   ]
  },
  {
   "path": "crdblab/insights/charts_b.py",
   "module": "crdblab.insights.charts_b",
   "doc": "Group B: hardware utilisation, from ``hardware_metrics.csv``.\n\nNodes are polled independently, so multi-node views bin onto a common time\naxis first. Tier-level figures (B2, B5) window each tier from the first\ninterval of its earliest repetition to the last of its latest, on the harness\nclock; with shuffled repetitions that window can span most of the sweep.",
   "lines": 345,
   "constants": [
    {
     "name": "HEATMAP_BINS",
     "value": "100",
     "doc": "Bins across a run for anything that puts every node on one time axis.",
     "line": 31
    },
    {
     "name": "SERIES_BIN_S",
     "value": "5.0",
     "doc": "Seconds per bin when averaging nodes into one per-cluster series.",
     "line": 33
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "_bench_hardware",
     "kind": "function",
     "signature": "(ctx: Context) -> dict[str, tuple]",
     "doc": "``{engine: (run, hardware frame)}`` for every bench run that recorded hardware.",
     "line": 36,
     "decorators": []
    },
    {
     "name": "_node_colors",
     "kind": "function",
     "signature": "(nodes: list[str]) -> dict[str, str]",
     "doc": "",
     "line": 55,
     "decorators": []
    },
    {
     "name": "_tier_windows",
     "kind": "function",
     "signature": "(run) -> pd.DataFrame",
     "doc": "``concurrency, start, end``: each tier's envelope across its repetitions.",
     "line": 60,
     "decorators": []
    },
    {
     "name": "_per_tier",
     "kind": "function",
     "signature": "(frame: pd.DataFrame, windows: pd.DataFrame, column: str) -> pd.Series",
     "doc": "Mean of ``column`` over the samples inside each tier's window.",
     "line": 73,
     "decorators": []
    },
    {
     "name": "b1_cpu_timeline",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 89,
     "decorators": [
      "chart('B1', 'Per-node CPU timeline', 'CPU busy per node across the benchmark sweep, one panel per engine. The client\\n    node is dotted and is not a cluster member; it is shown so a client-side\\n    bottleneck can be excluded rather than assumed away.')"
     ]
    },
    {
     "name": "b2_cpu_efficiency",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 127,
     "decorators": [
      "chart('B2', 'CPU efficiency', \"Delivered throughput divided by mean CPU busy across the five cluster nodes,\\n    per tier. This is the cost-of-replication figure that throughput alone cannot\\n    give: an engine can be faster and still be spending more machine to get there.\\n    The client node is excluded from the CPU mean. Note: each tier's CPU is averaged\\n    from the start of its first repetition to the end of its last, and repetitions\\n    run in shuffled order, so with more than one repetition per tier the CPU side of\\n    this ratio is close to the whole sweep's average.\")"
     ]
    },
    {
     "name": "_cluster_series",
     "kind": "function",
     "signature": "(cluster: pd.DataFrame, column: str) -> pd.Series",
     "doc": "Mean across nodes of one column, on :data:`SERIES_BIN_S` bins.",
     "line": 148,
     "decorators": []
    },
    {
     "name": "b3_disk",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 163,
     "decorators": [
      "chart('B3', 'Disk I/O and busy time', 'Mean per-node disk throughput and busy time across the cluster. At the current\\n    profile the working set is roughly 1.5x node RAM, so sustained read traffic here\\n    is the evidence that the benchmark is disk-bound rather than served from cache\\n    -- the assumption the whole profile rests on. A second axis carries busy %; it\\n    is dotted and grey because it is a different quantity, not a third series.')"
     ]
    },
    {
     "name": "b4_memory",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 204,
     "decorators": [
      "chart('B4', 'Memory headroom', 'Mean available memory across the five cluster nodes. Both engines are\\n    configured with a cache of a quarter of measured RAM, so this shows the OS page\\n    cache absorbing the remainder -- and how little headroom is left once a working\\n    set larger than RAM is being served.')"
     ]
    },
    {
     "name": "b5_replication_traffic",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 237,
     "decorators": [
      "chart('B5', 'Replication traffic amplification', \"Total bytes transmitted by the five cluster nodes divided by the operations they\\n    served, per tier. This is the closest direct measurement the testbed makes of\\n    what each replication design costs on the wire -- Raft's per-range replication\\n    against Patroni's WAL streaming. It is a ratio of two measured rates, so it is\\n    insensitive to the tiers having different durations. Read it as an order of\\n    magnitude: the link also carries Tailscale and node exporter traffic, which is\\n    not separated out. Note: the traffic is the mean per node, not the five-node\\n    total, and each tier's window runs from the start of its first repetition to the\\n    end of its last, so with more than one repetition per tier it is close to the\\n    whole sweep's average.\")"
     ]
    },
    {
     "name": "b6_heatmap",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 275,
     "decorators": [
      "chart('B6', 'Cluster utilisation heatmap', \"Every cluster node's CPU on one grid, binned onto a common time axis because the\\n    nodes are polled independently. A single bright row is a single busy machine; a\\n    uniformly lit panel is work spread across the cluster. One continuous hue, so\\n    brightness reads as magnitude rather than as category.\")"
     ]
    },
    {
     "name": "b7_imbalance",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 321,
     "decorators": [
      "chart('B7', 'Cluster load imbalance', \"Mean CPU per cluster node over the whole sweep, with a scale-free coefficient of\\n    variation recorded alongside so that an engine which simply runs hotter\\n    everywhere is not counted as imbalanced. Read this against the deployment rather\\n    than against the engines' reputations: this testbed deliberately pins\\n    CockroachDB's leaseholders to the gateway via lease_preferences and pins\\n    Patroni's primary to the same node, so neither arm is free to spread work as it\\n    otherwise might. What the chart measures is how much of the cluster each engine\\n    still uses under that pinning, which is a property of this configuration and\\n    must be quoted as one.\")"
     ]
    }
   ]
  },
  {
   "path": "crdblab/insights/charts_c.py",
   "module": "crdblab.insights.charts_c",
   "doc": "Group C: resilience, from the newest chaos run of each fault class, per engine.\n\nRecovery figures come from :mod:`crdblab.analysis.resilience`. Time axes use\nthe harness clock, on which the fault is recorded.",
   "lines": 410,
   "constants": [
    {
     "name": "OUTCOME_COLORS",
     "value": "{'ok': SERIES[2], 'timeout': '#e8a0a0', 'conn_error': CRITICAL, 'refused': WARNING}",
     "doc": "Probe outcome -> colour. ``ok`` is the only outcome that establishes the database was serving; the three failure outcomes keep their distinct meanings.",
     "line": 18
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "_chaos",
     "kind": "function",
     "signature": "(ctx: Context) -> list[tuple[str, str, object]]",
     "doc": "",
     "line": 26,
     "decorators": []
    },
    {
     "name": "_summaries",
     "kind": "function",
     "signature": "(ctx: Context) -> dict[str, dict]",
     "doc": "``resilience.availability`` / ``probe_availability`` per chaos run, computed once.",
     "line": 33,
     "decorators": []
    },
    {
     "name": "_runs",
     "kind": "function",
     "signature": "(chaos) -> list",
     "doc": "",
     "line": 49,
     "decorators": []
    },
    {
     "name": "_mark_fault",
     "kind": "function",
     "signature": "(ax, run) -> None",
     "doc": "",
     "line": 53,
     "decorators": []
    },
    {
     "name": "c1_probe_strip",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 69,
     "decorators": [
      "chart('C1', 'Probe attempt strip', 'One vertical rule per canary write, placed at the moment it completed and\\n    coloured by outcome. The outage is the blank band: this is the outage as\\n    directly observed, with no statistic between the reader and the measurement.\\n    Writes are placed by completion time, never dispatch time.')"
     ]
    },
    {
     "name": "c2_probe_latency",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 102,
     "decorators": [
      "chart('C2', 'Probe latency through the fault', 'Duration of every served canary write, log scale. A step in the floor after the\\n    fault is a structural change in the write path -- the new primary or\\n    leaseholder is a different distance away -- and is a separate finding from how\\n    long writes were unavailable.')"
     ]
    },
    {
     "name": "_label",
     "kind": "function",
     "signature": "(engine: str, mode: str) -> str",
     "doc": "",
     "line": 130,
     "decorators": []
    },
    {
     "name": "c3_instrument_agreement",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 142,
     "decorators": [
      "chart('C3', 'Instrument agreement', \"The same outage as measured by two instruments that share no code path, with\\n    each one's own sampling resolution drawn as its error bar. Agreement within\\n    those bars is the defensibility claim: a single instrument can be wrong in the\\n    flattering direction and nothing in its own output would show it.\")"
     ]
    },
    {
     "name": "c4_recovery_decomposition",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 188,
     "decorators": [
      "chart('C4', 'Recovery decomposition', 'The interval from the fault to the first blocked write is detection, not\\n    recovery -- the injected command returns before established connections stop\\n    working, so writes continue briefly after the fault is nominally in place.\\n    Separating the two prevents a fast failover from being credited with a slow\\n    detection.')"
     ]
    },
    {
     "name": "c5_post_fault_settling",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 222,
     "decorators": [
      "chart('C5', 'Post-fault settling', \"Throughput through the fault, with the post-fault mean and its one-sigma band.\\n    The verdict is the harness's own: a run whose coefficient of variation stays\\n    above 0.25 has not settled, and no recovery time can be stated for it -- which\\n    is a result to report, not a missing number.\")"
     ]
    },
    {
     "name": "c6_paths_after_failover",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 273,
     "decorators": [
      "chart('C6', 'Read and write paths after failover', 'Read and update medians either side of the fault, log scale. This is the\\n    asymmetry the cross-engine write-up must quote carefully: PostgreSQL\\'s clients\\n    follow the primary through HAProxy, so a promotion into another region moves the\\n    read path too -- and reads are 80% of this workload. A throughput RTO that never\\n    resolves is then a statement about where the new primary landed, not about the\\n    write path, and must be quoted with these read medians beside it. Note: the\\n    \"after\" median is taken from the moment of the fault, so it includes the outage\\'s\\n    own intervals, which record a p50 of 0; where the outage is a large share of what\\n    followed the fault, the \"after\" median understates the latency, down to 0.')"
     ]
    },
    {
     "name": "c7_hardware_through_failover",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 325,
     "decorators": [
      "chart('C7', 'Hardware through the failover', \"Per-node CPU across the fault, with the faulted node in the reserved status\\n    colour. The faulted node going quiet confirms the fault landed on the machine it\\n    was aimed at, and a survivor rising afterwards is the promotion visible in\\n    hardware rather than inferred from the database's own logs.\")"
     ]
    },
    {
     "name": "c8_rto_rpo",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 361,
     "decorators": [
      "chart('C8', 'RTO and RPO summary', 'Recovery time from both instruments and acknowledged writes lost, for every\\n    engine and fault class. RPO zero is the expected result for a quorum-replicated\\n    database and is meaningful only because the measurement could have shown\\n    otherwise: the audit client records what it was told committed and advances\\n    past ambiguous writes rather than retrying them.')"
     ]
    }
   ]
  },
  {
   "path": "crdblab/insights/charts_d.py",
   "module": "crdblab.insights.charts_d",
   "doc": "Group D: engine comparison, drawn from :func:`engine_comparison.compare`.\n\nUses the newest benchmark run of each engine. If the two runs are not\ncomparable, every chart here skips with the comparison's own reason.",
   "lines": 260,
   "constants": [
    {
     "name": "ARMS",
     "value": "{'cockroachdb': 'crdb', 'postgresql': 'pg'}",
     "doc": "",
     "line": 26
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "_pair",
     "kind": "function",
     "signature": "(ctx: Context)",
     "doc": "",
     "line": 29,
     "decorators": []
    },
    {
     "name": "_comparison",
     "kind": "function",
     "signature": "(ctx: Context) -> dict",
     "doc": "",
     "line": 40,
     "decorators": []
    },
    {
     "name": "d1_curves",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 63,
     "decorators": [
      "chart('D1', 'Engine throughput-latency curves', \"Each engine's own curve, annotated with the concurrency that produced each\\n    point. Read the horizontal distance as capacity and the vertical as cost; quote\\n    the saturation point rather than a single ratio, because the gap between the\\n    curves depends entirely on where along them it is measured.\")"
     ]
    },
    {
     "name": "_paired_bars",
     "kind": "function",
     "signature": "(ax, labels, crdb_values, pg_values) -> np.ndarray",
     "doc": "",
     "line": 89,
     "decorators": []
    },
    {
     "name": "d2_matched_throughput",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 108,
     "decorators": [
      "chart('D2', 'Latency at matched throughput', 'Both engines evaluated at throughputs each of them genuinely measured -- no\\n    extrapolation beyond either curve. The ratio above each pair is the overhead at\\n    that load; the starred point is the least confounded one, where the two engines\\n    were closest to the same fraction of their own capacity. Do not quote the\\n    largest ratio: it is the one where the slower engine is nearest saturation and\\n    so carries the most of its own queueing.')"
     ]
    },
    {
     "name": "d3_matched_utilisation",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 143,
     "decorators": [
      "chart('D3', 'Latency at matched utilisation', 'The engines held at the same fraction of their own measured capacity, so their\\n    queueing components are comparable and the residual is closer to the\\n    replication path alone. The throughputs beneath each pair are deliberately\\n    different -- that is what this framing holds variable -- so these bars must\\n    never be quoted as a cost at any particular ops/s.')"
     ]
    },
    {
     "name": "_outage",
     "kind": "function",
     "signature": "(ctx: Context, engine: str, mode: str)",
     "doc": "",
     "line": 170,
     "decorators": []
    },
    {
     "name": "d4_scorecard",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 186,
     "decorators": [
      "chart('D4', 'Engine scorecard', 'Each row normalised to the larger of the two values so that quantities in\\n    different units share an axis; the raw value is printed beside every bar because\\n    the normalised length alone is not quotable. The direction that counts as better\\n    differs by row and is stated on each one.')"
     ]
    },
    {
     "name": "d5_cost_of_consistency",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 234,
     "decorators": [
      "chart('D5', 'Cost of consistency', \"Each engine's write median at its lightest measured load, against the quorum\\n    floor measured independently by ping in Phase I. The floor is what the speed of\\n    light and this topology cost before any database is involved -- a 3-of-5 Raft\\n    quorum and Patroni's ANY 2 acknowledgement are the same geometry -- so the\\n    excess above it is the software's own contribution. Taken at the lightest load\\n    because that is where queueing contributes least.\")"
     ]
    }
   ]
  },
  {
   "path": "crdblab/insights/charts_e.py",
   "module": "crdblab.insights.charts_e",
   "doc": "Group E: network and provenance.\n\nE1, E2 and E4 draw the same Phase I matrix (:meth:`data.Inventory.network`).\nE3 draws the run inventory and what the loader said about each run.",
   "lines": 172,
   "constants": [],
   "classes": [],
   "functions": [
    {
     "name": "_network",
     "kind": "function",
     "signature": "(ctx: Context)",
     "doc": "",
     "line": 17,
     "decorators": []
    },
    {
     "name": "e1_quorum_floor",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 33,
     "decorators": [
      "chart('E1', 'Quorum floor derivation', 'Round-trip time from the gateway to every other node, sorted. A write commits\\n    once the leader and the two fastest acknowledgements have it, so the second bar\\n    sets the floor and the slower nodes do not gate an ordinary commit at all.\\n    Measured with ping, so it is independent of both databases -- which is what lets\\n    it bound them.')"
     ]
    },
    {
     "name": "e2_link_stability",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 73,
     "decorators": [
      "chart('E2', 'Link stability', \"Minimum to p99 for every ordered pair. A short bar is a link whose mean is a\\n    real description of it; a long one is a link whose mean is an average over two\\n    different behaviours, and any latency figure resting on it inherits that spread.\\n    Note that ping's printed precision degrades as RTT grows -- each link's own\\n    resolution is recorded in network.csv.\")"
     ]
    },
    {
     "name": "e3_run_provenance",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 108,
     "decorators": [
      "chart('E3', 'Run provenance', 'Every run directory this report considered. A run reaches a chart only by\\n    loading through the gated loader, which refuses anything without a manifest,\\n    with unexpected columns, or that fails pre-flight or validation -- so a run\\n    listed as refused here contributed to nothing in this document.')"
     ]
    },
    {
     "name": "e4_round_trip_matrix",
     "kind": "function",
     "signature": "(ctx: Context) -> Drawn",
     "doc": "",
     "line": 142,
     "decorators": [
      "chart('E4', 'Round-trip matrix', 'Mean round-trip time between every ordered pair of nodes, including the two\\n    neither E1 nor E2 puts on one axis with the rest: the non-gateway pairs. Darker\\n    is slower; a node does not ping itself, shown as a dash.')"
     ]
    }
   ]
  },
  {
   "path": "crdblab/insights/data.py",
   "module": "crdblab.insights.data",
   "doc": "Which runs a render considers, and the data the charts read from them.\n\nEvery run is loaded through the analysis loader, so a run without a manifest,\nor one failing validation or pre-flight, is recorded as refused with the\nloader's reason (drawn by chart E3). Selection is the newest passing run of\neach kind, per engine; run ids start with a UTC stamp, so name order is time order.",
   "lines": 189,
   "constants": [
    {
     "name": "KINDS",
     "value": "{'p1-network': 'network', 'bench_cluster': 'bench', 'p4-chaos-recover': 'chaos-recover', 'p4-chaos-dead': 'chaos-dead'}",
     "doc": "Run-directory suffix -> run kind. Standalone ``p4-probe`` runs are not charted.",
     "line": 22
    },
    {
     "name": "ENGINES",
     "value": "('cockroachdb', 'postgresql')",
     "doc": "Engines in the order every chart draws and lists them.",
     "line": 30
    },
    {
     "name": "MODES",
     "value": "('dead', 'recover')",
     "doc": "Fault classes in the order every chart draws and lists them.",
     "line": 33
    },
    {
     "name": "DEAD_COLUMNS",
     "value": "('gateway_cpu_pct', 'gateway_disk_iops', 'gateway_rss_bytes')",
     "doc": "``metrics.csv`` columns that are always blank (superseded by hardware_metrics.csv); named so nothing plots them as zero load.",
     "line": 37
    }
   ],
   "classes": [
    {
     "name": "RunEntry",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "One run directory the render considered, and what the gate said about it.",
     "line": 41,
     "fields": [
      {
       "name": "run_id",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "path",
       "type": "Path",
       "default": "",
       "doc": ""
      },
      {
       "name": "kind",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "engine",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "profile",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "run",
       "type": "Run | NetworkRun | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "refused",
       "type": "str",
       "default": "''",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "passing",
       "kind": "function",
       "signature": "(self) -> bool",
       "doc": "",
       "line": 53,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "mode",
       "kind": "function",
       "signature": "(self) -> str | None",
       "doc": "",
       "line": 57,
       "decorators": [
        "property"
       ]
      }
     ]
    },
    {
     "name": "Inventory",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "Every run of a chartable kind under ``runs_dir``, gated and indexed.",
     "line": 69,
     "fields": [
      {
       "name": "runs_dir",
       "type": "Path",
       "default": "",
       "doc": ""
      },
      {
       "name": "profile",
       "type": "str | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "entries",
       "type": "list[RunEntry]",
       "default": "field(default_factory=list)",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "scan",
       "kind": "function",
       "signature": "(cls, runs_dir: Path, profile: str | None=None) -> Inventory",
       "doc": "",
       "line": 77,
       "decorators": [
        "classmethod"
       ]
      },
      {
       "name": "latest",
       "kind": "function",
       "signature": "(self, kind: str, engine: str | None=None) -> Any",
       "doc": "The most recent passing run of ``kind`` (for ``engine``, if given).",
       "line": 106,
       "decorators": []
      },
      {
       "name": "per_engine",
       "kind": "function",
       "signature": "(self, kind: str) -> dict[str, Any]",
       "doc": "``{engine: newest passing run of kind}``, engines in :data:`ENGINES` order.",
       "line": 114,
       "decorators": []
      },
      {
       "name": "chaos",
       "kind": "function",
       "signature": "(self) -> list[tuple[str, str, Run]]",
       "doc": "``(engine, mode, run)`` for the newest chaos run of each class, per engine.",
       "line": 123,
       "decorators": []
      },
      {
       "name": "network",
       "kind": "function",
       "signature": "(self) -> NetworkRun | None",
       "doc": "The Phase I matrix the network charts draw: one deployment's, stated.\n\nThe newest matrix of each engine's deployment is found, and the earlier of\nthose is drawn. Charts E1, E2 and E4 all use this one run, so the three of\nthem always describe the same set of machines.",
       "line": 133,
       "decorators": []
      },
      {
       "name": "engines",
       "kind": "function",
       "signature": "(self) -> list[str]",
       "doc": "",
       "line": 144,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "passing",
       "kind": "function",
       "signature": "(self) -> list[RunEntry]",
       "doc": "",
       "line": 149,
       "decorators": [
        "property"
       ]
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_read_manifest",
     "kind": "function",
     "signature": "(path: Path) -> dict[str, Any] | None",
     "doc": "",
     "line": 61,
     "decorators": []
    },
    {
     "name": "hardware",
     "kind": "function",
     "signature": "(run: Run) -> pd.DataFrame | None",
     "doc": "A run's per-node hardware samples, ready to plot, or ``None`` if absent.\n\nEach node's first row has no rates (nothing to difference) and is dropped.",
     "line": 153,
     "decorators": []
    },
    {
     "name": "cluster_only",
     "kind": "function",
     "signature": "(frame: pd.DataFrame) -> pd.DataFrame",
     "doc": "Drop the client node: it is not a cluster member and carries no replica.",
     "line": 169,
     "decorators": []
    },
    {
     "name": "probe_attempts",
     "kind": "function",
     "signature": "(run: Run) -> pd.DataFrame | None",
     "doc": "The RTO probe's attempt log for a chaos run, or ``None`` if it has none.\n\nRead only after :func:`load_run` has validated it: the loader refuses a run\nwhose probe log fails its checks, so reaching this means the file is sound.",
     "line": 174,
     "decorators": []
    },
    {
     "name": "fault_offset",
     "kind": "function",
     "signature": "(run: Run) -> float | None",
     "doc": "When the fault landed, on the harness clock, from the run's own events.",
     "line": 186,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/insights/report.py",
   "module": "crdblab.insights.report",
   "doc": "The files a render writes beside its charts.\n\n``insights.md`` and a self-contained ``dashboard.html`` (SVGs inlined) carry\nevery chart with its caption and numbers, the runs behind them, and every\nskipped chart with its reason. ``summary.json``, ``summary.csv`` and\n``chart_status.csv`` hold the same record in machine-readable form.",
   "lines": 306,
   "constants": [
    {
     "name": "_CSS",
     "value": "'\\n:root {\\n  --surface: #fcfcfb; --panel: #ffffff; --ink: #0b0b0b; --ink-2: #52514e;\\n  --ink-3: #898781; --line: #e1e0d9; --accent: #2a78d6; --warn: #b8860...",
     "doc": "",
     "line": 152
    }
   ],
   "classes": [
    {
     "name": "Result",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 25,
     "fields": [
      {
       "name": "chart",
       "type": "Chart",
       "default": "",
       "doc": ""
      },
      {
       "name": "drawn",
       "type": "bool",
       "default": "",
       "doc": ""
      },
      {
       "name": "stats",
       "type": "dict[str, Any]",
       "default": "",
       "doc": ""
      },
      {
       "name": "files",
       "type": "list[str]",
       "default": "",
       "doc": ""
      },
      {
       "name": "skipped",
       "type": "str",
       "default": "''",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "png",
       "kind": "function",
       "signature": "(self) -> str | None",
       "doc": "",
       "line": 33,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "svg",
       "kind": "function",
       "signature": "(self) -> str | None",
       "doc": "",
       "line": 37,
       "decorators": [
        "property"
       ]
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "fmt",
     "kind": "function",
     "signature": "(value: Any) -> str",
     "doc": "One stat as a report cell: four significant figures, thousands separated.\n\nIntegers print exactly; dicts become ``key=value`` pairs; lists show their length.",
     "line": 41,
     "decorators": []
    },
    {
     "name": "flatten",
     "kind": "function",
     "signature": "(stats: Any, prefix: str='') -> list[tuple[str, Any]]",
     "doc": "Nested stats as ``(metric, value)`` rows: keys join with ``.``, lists index ``[n]``.",
     "line": 61,
     "decorators": []
    },
    {
     "name": "_json_safe",
     "kind": "function",
     "signature": "(value: Any) -> Any",
     "doc": "",
     "line": 75,
     "decorators": []
    },
    {
     "name": "_scope",
     "kind": "function",
     "signature": "(profile: str | None) -> str",
     "doc": "",
     "line": 87,
     "decorators": []
    },
    {
     "name": "_where",
     "kind": "function",
     "signature": "(out_dir: Path) -> str",
     "doc": "",
     "line": 91,
     "decorators": []
    },
    {
     "name": "write_markdown",
     "kind": "function",
     "signature": "(out_dir: Path, results: list[Result], inventory: Inventory, generated: datetime) -> Path",
     "doc": "",
     "line": 97,
     "decorators": []
    },
    {
     "name": "_inline_svg",
     "kind": "function",
     "signature": "(path: Path) -> str",
     "doc": "An SVG file's ``<svg>`` element, without the XML prolog and doctype.",
     "line": 201,
     "decorators": []
    },
    {
     "name": "write_dashboard",
     "kind": "function",
     "signature": "(out_dir: Path, results: list[Result], inventory: Inventory, generated: datetime) -> Path",
     "doc": "",
     "line": 208,
     "decorators": []
    },
    {
     "name": "write_summary",
     "kind": "function",
     "signature": "(out_dir: Path, results: list[Result]) -> list[Path]",
     "doc": "",
     "line": 271,
     "decorators": []
    },
    {
     "name": "one_line",
     "kind": "function",
     "signature": "(text: str) -> str",
     "doc": "",
     "line": 305,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/insights/run.py",
   "module": "crdblab.insights.run",
   "doc": "Render the whole chart catalogue into one directory.\n\nThe caller chooses a fresh ``out_dir`` per render (``cli._cmd_insights`` does).",
   "lines": 58,
   "constants": [],
   "classes": [],
   "functions": [
    {
     "name": "generate",
     "kind": "function",
     "signature": "(out_dir: Path, runs_dir: Path, profile: str | None=None, progress: Callable[[Result], None] | None=None) -> tuple[list[Result], Inventory]",
     "doc": "Draw every chart whose inputs exist, and write the report beside them.\n\nA chart that cannot draw, or raises, is recorded as skipped with the reason,\nso one broken chart never costs the rest.",
     "line": 22,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/phases/__init__.py",
   "module": "crdblab.phases",
   "doc": "",
   "lines": 0,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/phases/bench.py",
   "module": "crdblab.phases.bench",
   "doc": "Phase II: steady-state throughput and latency, swept over concurrency tiers.\n\nThe generator runs on the dedicated client node against one connection string\n(``cockroach workload`` dials multiple URLs serially, which is very slow):\nthe gateway for CockroachDB, the client node's pgbouncer/HAProxy for PostgreSQL.\nThe sweep is otherwise identical for both engines.\n\n* Output is parsed by the strict, header-bound\n  :class:`~crdblab.core.workload.WorkloadParser`.\n* Tier order is shuffled with a profile-seeded RNG and recorded in the manifest.\n* Scheduling uses :func:`time.monotonic`.\n* Each tier is bracketed by a row-match probe, and its write median is checked\n  against the Phase I quorum floor, so a workload touching no rows is caught.",
   "lines": 377,
   "constants": [],
   "classes": [
    {
     "name": "Target",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "What is under test, and where the generator runs.\n\n``voters`` is the replication factor, which sets the quorum floor.",
     "line": 41,
     "fields": [
      {
       "name": "name",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "phase",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "exec_node",
       "type": "Node",
       "default": "",
       "doc": ""
      },
      {
       "name": "database",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "voters",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "engine",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "nodes",
       "type": "tuple[Node, ...]",
       "default": "()",
       "doc": ""
      },
      {
       "name": "password",
       "type": "str | None",
       "default": "None",
       "doc": "PostgreSQL ``root`` password (CockroachDB runs ``--insecure``)."
      }
     ],
     "methods": [
      {
       "name": "db_uri",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "The single connection string the generator dials.",
       "line": 58,
       "decorators": [
        "property"
       ]
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "tier_order",
     "kind": "function",
     "signature": "(profile: Profile) -> list[tuple[int, int]]",
     "doc": "Realised (concurrency, repetition) sequence for the sweep.\n\nShuffled with an RNG seeded from the profile, so the order is reproducible.",
     "line": 67,
     "decorators": []
    },
    {
     "name": "_run_tier",
     "kind": "function",
     "signature": "(target: Target, profile: Profile, concurrency: int, repetition: int, raw_path: Path, writer: MetricsWriter, manifest: Manifest, t_zero: float, tier_index: int=0, tier_total: int=0) -> dict[str, Any]",
     "doc": "Execute one tier, record its per-interval samples, and return its summary.",
     "line": 79,
     "decorators": []
    },
    {
     "name": "run",
     "kind": "function",
     "signature": "(settings: Settings, profile: Profile, target: Target, network_run: Path | None=None, skip_checks: bool=False) -> tuple[RunDirectory, dict[str, Any]]",
     "doc": "Execute a full concurrency sweep against ``target``.",
     "line": 217,
     "decorators": []
    },
    {
     "name": "cluster_target",
     "kind": "function",
     "signature": "(settings: Settings, database: str='ycsb', engine: str='cockroachdb') -> Target",
     "doc": "The five-node cluster, driven from the dedicated client node.",
     "line": 366,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/phases/p1_network.py",
   "module": "crdblab.phases.p1_network",
   "doc": "Phase I: characterise the network substrate.\n\nMeasures the all-pairs RTT matrix over Tailscale. From it comes the quorum\nfloor: a write committed by 3 of 5 voters cannot be faster than the round trip\nto the second-fastest follower. Each source pings every destination in one SSH\nsession, all sources in parallel.",
   "lines": 278,
   "constants": [
    {
     "name": "PING_COUNT",
     "value": "100",
     "doc": "100 samples at 0.1 s: ten seconds per source, all sources concurrently.",
     "line": 31
    },
    {
     "name": "PING_INTERVAL_S",
     "value": "0.1",
     "doc": "",
     "line": 32
    },
    {
     "name": "_TIME_RE",
     "value": "re.compile('time=([0-9.]+)\\\\s*ms')",
     "doc": "",
     "line": 34
    },
    {
     "name": "_LOSS_RE",
     "value": "re.compile('([0-9.]+)%\\\\s+packet loss')",
     "doc": "",
     "line": 35
    },
    {
     "name": "_SECTION_RE",
     "value": "re.compile('^==PING:(?P<dest>[^=]+)==$')",
     "doc": "",
     "line": 36
    },
    {
     "name": "_SUMMARY_RE",
     "value": "re.compile('rtt\\\\s+min/avg/max/mdev\\\\s*=\\\\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\\\\s*ms')",
     "doc": "ping's own summary, which carries three decimals irrespective of magnitude.",
     "line": 38
    }
   ],
   "classes": [
    {
     "name": "LinkStats",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 45,
     "fields": [
      {
       "name": "samples",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "loss_pct",
       "type": "float",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_min_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_mean_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_p50_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_p95_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_p99_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_max_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_mdev_ms",
       "type": "float | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "rtt_resolution_ms",
       "type": "float | None",
       "default": "",
       "doc": "Precision of the per-packet values the quantiles were computed from."
      }
     ],
     "methods": []
    },
    {
     "name": "NodeProbe",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 60,
     "fields": [
      {
       "name": "node",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "mtu",
       "type": "int | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "links",
       "type": "dict[str, LinkStats]",
       "default": "field(default_factory=dict)",
       "doc": ""
      },
      {
       "name": "error",
       "type": "str | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "raw",
       "type": "str",
       "default": "''",
       "doc": "Verbatim remote output, saved under ``raw/``."
      }
     ],
     "methods": []
    }
   ],
   "functions": [
    {
     "name": "_quantile",
     "kind": "function",
     "signature": "(ordered: list[float], q: float) -> float",
     "doc": "Nearest-rank quantile.",
     "line": 69,
     "decorators": []
    },
    {
     "name": "parse_ping",
     "kind": "function",
     "signature": "(output: str) -> LinkStats",
     "doc": "Summarise one ping run.\n\nMin/mean/max/mdev come from ping's summary line (always three decimals).\nQuantiles come from per-packet lines, whose precision drops as RTT grows,\nso that precision is recorded as ``rtt_resolution_ms``. A destination with\nno replies is recorded as 100% loss rather than omitted.",
     "line": 77,
     "decorators": []
    },
    {
     "name": "_remote_script",
     "kind": "function",
     "signature": "(source: Node, destinations: list[Node]) -> str",
     "doc": "",
     "line": 121,
     "decorators": []
    },
    {
     "name": "probe_node",
     "kind": "function",
     "signature": "(source: Node, destinations: list[Node]) -> NodeProbe",
     "doc": "Run the full destination sweep from one source in a single SSH session.",
     "line": 134,
     "decorators": []
    },
    {
     "name": "run",
     "kind": "function",
     "signature": "(settings: Settings, profile: Profile, topology: Topology | None=None, engine: str='cockroachdb') -> tuple[RunDirectory, list[NodeProbe]]",
     "doc": "Execute Phase I and record it as an immutable run directory.\n\n``engine`` does not affect ping, but records which deployment was measured.",
     "line": 181,
     "decorators": []
    },
    {
     "name": "summarise",
     "kind": "function",
     "signature": "(probes: list[NodeProbe], topo: Topology) -> dict[str, Any]",
     "doc": "Derive the quantities later phases depend on.",
     "line": 262,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/phases/p4_chaos.py",
   "module": "crdblab.phases.p4_chaos",
   "doc": "Phases III-IV: fault injection, and the measurement of RTO and RPO.\n\nPhase III is ``recover`` (a self-healing network partition); Phase IV is\n``dead`` (the database process is killed). A steady-state workload runs from\nthe client node while the fault is injected into the node leading the write\npath. Three independent clients observe it:\n\n* the **generator**: throughput per second, giving the performance RTO;\n* the **audit writer**: a serial sequence of writes, each classified as\n  acknowledged, ambiguous or refused, giving the RPO and an availability RTO;\n* the **RTO probe** (on the client node): several concurrent canary writes,\n  giving a finer-grained availability RTO.\n\nThe fault is scheduled on a monotonic clock by a timer thread, measured from\nthe generator's first sample. Recovery is the *start* of a window in which\nthroughput holds above the threshold for ``recovery_hold_s``.",
   "lines": 1092,
   "constants": [
    {
     "name": "MODES",
     "value": "('dead', 'recover')",
     "doc": "``dead`` kills the process; ``recover`` partitions the node then heals it.",
     "line": 50
    },
    {
     "name": "PACKAGE_ROOT",
     "value": "Path(__file__).resolve().parents[2]",
     "doc": "Checkout root; the probe agent's source is copied from here each run.",
     "line": 53
    },
    {
     "name": "PROBE_OVERRUN_S",
     "value": "600.0",
     "doc": "Dead-man switch added to the agent's lifetime. The harness normally stops it; this only stops an orphaned agent. Generous because generator setup can be slow.",
     "line": 57
    },
    {
     "name": "SUDO",
     "value": "ssh.SUDO",
     "doc": "",
     "line": 59
    },
    {
     "name": "PG_RESTART_OVERRIDE",
     "value": "'/etc/systemd/system/patroni.service.d/99-crdblab-chaos.conf'",
     "doc": "systemd drop-in that sets ``Restart=no`` so a killed Patroni stays dead instead of being restarted in ~100 ms. Removed again by :func:`restore_target`.",
     "line": 64
    },
    {
     "name": "RECOVER_HEAL_DELAY_S",
     "value": "45",
     "doc": "How long the ``recover`` partition lasts; also used by :func:`generator_duration_s`.",
     "line": 67
    },
    {
     "name": "_PG_DEAD_PAYLOAD",
     "value": "f\"{SUDO} mkdir -p {PG_RESTART_OVERRIDE.rsplit('/', 1)[0]} && printf '[Service]\\\\nRestart=no\\\\n' | {SUDO} tee {PG_RESTART_OVERRIDE} >/dev/null && {SUDO} syste...",
     "doc": "",
     "line": 70
    },
    {
     "name": "PATRONI_PRIMARY_PORT",
     "value": "preflight.PATRONI_PRIMARY_PORT",
     "doc": "Re-exported from ``core.preflight``, which owns primary resolution.",
     "line": 118
    },
    {
     "name": "PATRONI_PRIMARY_TIMEOUT_S",
     "value": "preflight.PATRONI_PRIMARY_TIMEOUT_S",
     "doc": "",
     "line": 119
    },
    {
     "name": "COVERAGE_SLACK_CADENCES",
     "value": "10.0",
     "doc": "How far (in write cadences) the last acknowledgement may fall short of the run's end before the audit writer counts as having stopped observing.",
     "line": 444
    }
   ],
   "classes": [
    {
     "name": "AuditResult",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 124,
     "fields": [
      {
       "name": "acknowledged",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "ambiguous",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "refused",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "present",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "lost",
       "type": "list[int]",
       "default": "",
       "doc": ""
      },
      {
       "name": "ambiguous_committed",
       "type": "int",
       "default": "",
       "doc": ""
      },
      {
       "name": "first_ack_utc",
       "type": "str | None",
       "default": "",
       "doc": ""
      },
      {
       "name": "last_ack_utc",
       "type": "str | None",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "rpo_violations",
       "kind": "function",
       "signature": "(self) -> int",
       "doc": "",
       "line": 135,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "to_dict",
       "kind": "function",
       "signature": "(self) -> dict[str, Any]",
       "doc": "",
       "line": 138,
       "decorators": []
      }
     ]
    },
    {
     "name": "AuditWriter",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "Writes a monotonic sequence continuously, recording the client's view.\n\nEach write is *acknowledged*, *ambiguous* (connection failed after sending;\nmay or may not have committed) or *refused*. Only an acknowledged write\nlater missing from the table is data loss. Sequence numbers are never retried.",
     "line": 152,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, dsn: str, interval_s: float) -> None",
       "doc": "",
       "line": 160,
       "decorators": []
      },
      {
       "name": "_loop",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 179,
       "decorators": []
      },
      {
       "name": "__enter__",
       "kind": "function",
       "signature": "(self) -> AuditWriter",
       "doc": "",
       "line": 221,
       "decorators": []
      },
      {
       "name": "__exit__",
       "kind": "function",
       "signature": "(self, *exc) -> None",
       "doc": "",
       "line": 226,
       "decorators": []
      },
      {
       "name": "collect",
       "kind": "function",
       "signature": "(self, dsn: str) -> AuditResult",
       "doc": "Compare the client's record against what the database actually holds.",
       "line": 233,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "get_payload",
     "kind": "function",
     "signature": "(mode: str, engine: str) -> str",
     "doc": "The shell command that injects the fault.\n\nPostgreSQL's ``dead`` fault disables systemd restarts, then kills the whole\nunit's cgroup (Patroni runs as ``python3``, so ``killall`` would miss it).",
     "line": 78,
     "decorators": []
    },
    {
     "name": "preflight_payload",
     "kind": "function",
     "signature": "(mode: str, engine: str) -> str",
     "doc": "A harmless command needing the same privileges as the fault.\n\n``recover``'s payload is backgrounded, so its exit status cannot show the\nfault landed; this checks permission before the run instead.",
     "line": 98,
     "decorators": []
    },
    {
     "name": "check_fault_authorisation",
     "kind": "function",
     "signature": "(report: preflight.PreflightReport, node: Node, mode: str, engine: str) -> None",
     "doc": "Pre-flight: the fault must be permitted on ``node``.\n\nA denied injection still yields a full run that measures an undisturbed\ncluster and looks like excellent resilience, so it is caught up front.",
     "line": 253,
     "decorators": []
    },
    {
     "name": "generator_duration_s",
     "kind": "function",
     "signature": "(chaos: Any, mode: str='dead') -> int",
     "doc": "How long the generator must run to leave a usable post-fault series.\n\n``inject_at_s`` counts from the generator's first sample, so the run is\nextended to ``inject_at_s + min_post_fault_s`` (plus the heal delay in\n``recover`` mode, so the demoted node is back before the next phase).\nNever shortened.",
     "line": 304,
     "decorators": []
    },
    {
     "name": "restore_target",
     "kind": "function",
     "signature": "(node: Node, topo: Topology, engine: str, timeout_s: float=120.0, poll_interval_s: float=5.0) -> dict[str, Any]",
     "doc": "Bring the ``dead`` fault target back and confirm it rejoined.\n\nCalled only after every artefact is written, and recorded as its own\nevent, so the repair cannot affect the measurement. Uses sudo (the store is\nroot-owned), polls liveness from a surviving node, and redirects the\nremote output so ``--background`` does not hold the SSH session open.",
     "line": 319,
     "decorators": []
    },
    {
     "name": "inject_fault",
     "kind": "function",
     "signature": "(node: Node, mode: str, engine: str) -> dict[str, Any]",
     "doc": "Apply the fault and return when it was applied.\n\nThe timestamp is taken before the call. A transport error is not failure:\na ``dead`` fault often kills the connection it arrived on.",
     "line": 410,
     "decorators": []
    },
    {
     "name": "availability_rto",
     "kind": "function",
     "signature": "(attempts: list[tuple[float, int, str]], fault_monotonic: float, observation_end: float | None=None) -> dict[str, Any]",
     "doc": "Time from the fault until the database accepted a write again.\n\nDistinct from the throughput-based :func:`find_recovery`: a cluster can\naccept writes again within seconds but never regain its old throughput.\nThe outage is the largest gap between acknowledged writes that closes after\nthe fault and exceeds the healthy noise floor. Resolution is bounded by the\naudit cadence (~one quorum write) and is returned alongside.\n\nWith ``observation_end``, coverage is checked: if acknowledgements stop well\nbefore the run ends, no RTO is stated (unmeasured, not zero).",
     "line": 447,
     "decorators": []
    },
    {
     "name": "clock_offsets",
     "kind": "function",
     "signature": "(observed_at: dict[float, float]) -> dict[str, Any]",
     "doc": "Summarise the offset between the generator's clock and the harness's.\n\n``observed_at`` maps generator ``elapsed`` to harness-clock offset. Their\ndifference (~5 s of SSH and process startup) is reported with its spread; a\nsmall spread shows the clocks run at the same rate.",
     "line": 564,
     "decorators": []
    },
    {
     "name": "find_recovery",
     "kind": "function",
     "signature": "(ticks: list[tuple[float, float]], fault_at_s: float, baseline_tps: float, threshold: float, hold_s: float) -> float | None",
     "doc": "First offset after the fault at which throughput holds above the threshold.\n\n``ticks`` is ``(offset_s, total_tps)``. Returns the *start* of the first\nwindow of ``hold_s`` seconds at or above ``baseline_tps * threshold``.",
     "line": 590,
     "decorators": []
    },
    {
     "name": "_optional",
     "kind": "function",
     "signature": "(resource)",
     "doc": "Enter ``resource`` if it is not ``None``.",
     "line": 614,
     "decorators": [
      "contextmanager"
     ]
    },
    {
     "name": "run",
     "kind": "function",
     "signature": "(settings: Settings, profile: Profile, mode: str, database: str='ycsb', audit_database: str='bench', engine: str='cockroachdb') -> tuple[RunDirectory, dict[str, Any]]",
     "doc": "Drive a steady-state workload, inject a fault, and measure RTO and RPO.",
     "line": 623,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/report/__init__.py",
   "module": "crdblab.report",
   "doc": "",
   "lines": 0,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "crdblab/report/figures.py",
   "module": "crdblab.report.figures",
   "doc": "Dissertation figures (``crdblab report figures``), rendered from validated runs.\n\nEvery input is loaded through :mod:`crdblab.analysis.loader`, so only runs\nwith a manifest that pass validation are drawn. Each figure stamps its source\nrun ids in its footer and its filename (:func:`_provenance_slug`), and is\nwritten as a PNG at :data:`EXPORT_WIDTH_PX` plus an SVG. Aggregation comes\nfrom the analysis layer, never recomputed here.\n\nIn the resilience timeline the fault is a line when the clock offset was\nmeasured, and a band of the unmeasured width when it was only bounded.",
   "lines": 306,
   "constants": [
    {
     "name": "_RESILIENCE_FIGURES",
     "value": "{'dead': 'fig5_resilience_timeline', 'recover': 'fig6_resilience_timeline_recover'}",
     "doc": "Filename stem per fault class; the numbers stay fixed so captions stay valid.",
     "line": 191
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "network_matrix",
     "kind": "function",
     "signature": "(run: NetworkRun, out_dir: Path) -> Path",
     "doc": "All-pairs round-trip matrix.\n\nSingle-hue heatmap, darker for slower, with values printed in each cell.",
     "line": 43,
     "decorators": []
    },
    {
     "name": "throughput_sweep",
     "kind": "function",
     "signature": "(runs: Sequence[Run], out_dir: Path) -> Path",
     "doc": "Throughput against offered concurrency, with an interval where one exists.\n\nError bars are the Student's t 95% interval over *repetitions*, and are\nabsent for a single-repetition tier rather than drawn as zero: a zero-width\ninterval asserts agreement between repetitions that were never run.",
     "line": 95,
     "decorators": []
    },
    {
     "name": "latency_by_operation",
     "kind": "function",
     "signature": "(run: Run, out_dir: Path) -> Path",
     "doc": "Per-operation latency by tier, as small multiples.\n\nRead and write latency differ by ~100x, so each op gets its own panel.",
     "line": 148,
     "decorators": []
    },
    {
     "name": "_resilience_filename",
     "kind": "function",
     "signature": "(mode: str | None, provenance_slug: str='') -> str",
     "doc": "Filename for one fault class, with the provenance slug before the extension.",
     "line": 197,
     "decorators": []
    },
    {
     "name": "resilience_timeline",
     "kind": "function",
     "signature": "(run: Run, out_dir: Path) -> Path",
     "doc": "Throughput through a fault, on a single, explicitly stated clock.\n\nWith a measured clock offset the x-axis is the harness clock and the fault a\nline; with only a bounded offset it is the generator clock and the fault a\nband spanning the uncertainty.",
     "line": 208,
     "decorators": []
    },
    {
     "name": "render_all",
     "kind": "function",
     "signature": "(out_dir: Path, network: NetworkRun | None=None, cluster: Run | None=None, chaos: Run | Sequence[Run] | None=None) -> list[Path]",
     "doc": "Render every figure whose inputs are available.\n\n``chaos`` may be a sequence: one timeline per fault class.",
     "line": 286,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/report/style.py",
   "module": "crdblab.report.style",
   "doc": "Shared figure style: palette, rcParams, provenance filenames and PNG+SVG export.\n\nUsed by both :mod:`crdblab.report.figures` and :mod:`crdblab.insights`. Figures\nare for print on a light background; series differ by hue *and* by marker and\ndash, so they survive greyscale printing.",
   "lines": 148,
   "constants": [
    {
     "name": "SURFACE",
     "value": "'#fcfcfb'",
     "doc": "",
     "line": 20
    },
    {
     "name": "INK",
     "value": "'#0b0b0b'",
     "doc": "",
     "line": 21
    },
    {
     "name": "INK_SECONDARY",
     "value": "'#52514e'",
     "doc": "",
     "line": 22
    },
    {
     "name": "INK_MUTED",
     "value": "'#898781'",
     "doc": "",
     "line": 23
    },
    {
     "name": "GRID",
     "value": "'#e1e0d9'",
     "doc": "",
     "line": 24
    },
    {
     "name": "AXIS",
     "value": "'#c3c2b7'",
     "doc": "",
     "line": 25
    },
    {
     "name": "SERIES",
     "value": "('#2a78d6', '#eb6834', '#1baf7a')",
     "doc": "",
     "line": 27
    },
    {
     "name": "MARKERS",
     "value": "('o', 's', '^')",
     "doc": "",
     "line": 28
    },
    {
     "name": "DASHES",
     "value": "('-', '--', '-.')",
     "doc": "",
     "line": 29
    },
    {
     "name": "CRITICAL",
     "value": "'#d03b3b'",
     "doc": "",
     "line": 30
    },
    {
     "name": "WARNING",
     "value": "'#fab219'",
     "doc": "",
     "line": 31
    },
    {
     "name": "BLUE_RAMP",
     "value": "['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']",
     "doc": "Single-hue sequential ramp for magnitudes.",
     "line": 34
    },
    {
     "name": "SEQUENTIAL",
     "value": "LinearSegmentedColormap.from_list('crdblab_blue', BLUE_RAMP)",
     "doc": "",
     "line": 35
    },
    {
     "name": "EXPORT_WIDTH_PX",
     "value": "3840",
     "doc": "Minimum exported width in pixels (4K). Reached by raising export DPI, never by enlarging the figure, so the layout is unchanged.",
     "line": 39
    },
    {
     "name": "EXPORT_VECTOR_EXT",
     "value": "'.svg'",
     "doc": "Vector copy written beside every PNG.",
     "line": 42
    }
   ],
   "classes": [],
   "functions": [
    {
     "name": "_slug",
     "kind": "function",
     "signature": "(value: object) -> str",
     "doc": "Filename-safe form of one provenance component.",
     "line": 45,
     "decorators": []
    },
    {
     "name": "_manifest_field",
     "kind": "function",
     "signature": "(run, *path: str, default: str='unknown') -> str",
     "doc": "One nested manifest value, for either a ``Run`` or a ``NetworkRun``.\n\nRead from the manifest rather than from a property because the two run types\ndo not share one: ``Run`` exposes ``.engine`` and ``.profile``, ``NetworkRun``\nexposes neither, and both carry the manifest itself.",
     "line": 52,
     "decorators": []
    },
    {
     "name": "_provenance_slug",
     "kind": "function",
     "signature": "(*runs) -> str",
     "doc": "The filename tail naming the engine, profile and run(s) behind a figure.\n\nE.g. ``_cockroachdb_thesis_<run_id>``. Where runs disagree, the component is\n``mixed-engine`` or ``mixed-profile``; every run id is always listed.",
     "line": 67,
     "decorators": []
    },
    {
     "name": "_style",
     "kind": "function",
     "signature": "() -> None",
     "doc": "Recessive chrome: hairline solid grid, no top/right spines, sans text.",
     "line": 82,
     "decorators": []
    },
    {
     "name": "_finish",
     "kind": "function",
     "signature": "(fig, ax_or_axes, provenance: Sequence[str], path: Path) -> Path",
     "doc": "Strip the top/right spines and stamp the run ids the figure came from.",
     "line": 114,
     "decorators": []
    },
    {
     "name": "_written_formats",
     "kind": "function",
     "signature": "(png: Path) -> list[Path]",
     "doc": "Every file :func:`_finish` wrote for one figure, for the caller to report.",
     "line": 146,
     "decorators": []
    }
   ]
  },
  {
   "path": "crdblab/topology.py",
   "module": "crdblab.topology",
   "doc": "Single source of truth for the testbed topology: nodes, logins and regions.",
   "lines": 91,
   "constants": [
    {
     "name": "DEFAULT_TOPOLOGY",
     "value": "Topology(nodes=(Node('linode-1', 'crdb-linode-1', 'root', 'linode', 'us-east', 'cloud=linode,region=us-east'), Node('linode-2', 'crdb-linode-2', 'root', 'lin...",
     "doc": "The five-node, three-provider cluster. gcp-1 is the gateway: the CockroachDB leaseholder and Patroni primary are pinned there. Localities must match terraform/scripts/.",
     "line": 72
    },
    {
     "name": "CLIENT_NODE",
     "value": "Node('client-1', 'crdb-client-1', 'ubuntu', 'gcp', 'us-east1', 'cloud=gcp,region=us-east1')",
     "doc": "Dedicated workload-generator node; not a cluster member.",
     "line": 88
    }
   ],
   "classes": [
    {
     "name": "Node",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "One testbed machine.\n\n``locality`` mirrors ``cockroach start --locality`` and is recorded in each\nrun manifest.",
     "line": 10,
     "fields": [
      {
       "name": "name",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "host",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "user",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "provider",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "region",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "locality",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "gateway",
       "type": "bool",
       "default": "False",
       "doc": ""
      },
      {
       "name": "sql_port",
       "type": "int",
       "default": "26257",
       "doc": ""
      },
      {
       "name": "http_port",
       "type": "int",
       "default": "8080",
       "doc": ""
      },
      {
       "name": "node_exporter_port",
       "type": "int",
       "default": "9100",
       "doc": "Default port of the Ubuntu-packaged prometheus-node-exporter."
      }
     ],
     "methods": [
      {
       "name": "http_base",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 30,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "node_exporter_url",
       "kind": "function",
       "signature": "(self) -> str",
       "doc": "",
       "line": 34,
       "decorators": [
        "property"
       ]
      }
     ]
    },
    {
     "name": "Topology",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "",
     "line": 39,
     "fields": [
      {
       "name": "nodes",
       "type": "tuple[Node, ...]",
       "default": "",
       "doc": ""
      }
     ],
     "methods": [
      {
       "name": "__iter__",
       "kind": "function",
       "signature": "(self) -> Iterator[Node]",
       "doc": "",
       "line": 42,
       "decorators": []
      },
      {
       "name": "__len__",
       "kind": "function",
       "signature": "(self) -> int",
       "doc": "",
       "line": 45,
       "decorators": []
      },
      {
       "name": "names",
       "kind": "function",
       "signature": "(self) -> tuple[str, ...]",
       "doc": "",
       "line": 49,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "get",
       "kind": "function",
       "signature": "(self, name: str) -> Node",
       "doc": "",
       "line": 52,
       "decorators": []
      },
      {
       "name": "gateway",
       "kind": "function",
       "signature": "(self) -> Node",
       "doc": "",
       "line": 59,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "from_mapping",
       "kind": "function",
       "signature": "(cls, raw: Mapping) -> Topology",
       "doc": "",
       "line": 66,
       "decorators": [
        "classmethod"
       ]
      }
     ]
    }
   ],
   "functions": []
  },
  {
   "path": "pipeline/__init__.py",
   "module": "pipeline",
   "doc": "Full-pipeline orchestration: provision, measure, tear down, for both engines.",
   "lines": 1,
   "constants": [],
   "classes": [],
   "functions": []
  },
  {
   "path": "pipeline/nodes.py",
   "module": "pipeline.nodes",
   "doc": "The six testbed VMs, reached over SSH via Tailscale.\n\nResolved from :mod:`crdblab.topology`, the same source ``run-experiment.sh`` reads.",
   "lines": 49,
   "constants": [
    {
     "name": "SSH_OPTS",
     "value": "['-q', '-n', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']",
     "doc": "Matches run-experiment.sh's SSH_OPTS: hosts are rebuilt and addresses reused, and -n stops ssh from swallowing stdin.",
     "line": 15
    }
   ],
   "classes": [
    {
     "name": "Vm",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass(frozen=True)"
     ],
     "doc": "",
     "line": 25,
     "fields": [
      {
       "name": "user",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "host",
       "type": "str",
       "default": "",
       "doc": ""
      }
     ],
     "methods": []
    }
   ],
   "functions": [
    {
     "name": "vms",
     "kind": "function",
     "signature": "() -> list[Vm]",
     "doc": "Every VM Terraform creates: the five cluster members and the client node.",
     "line": 30,
     "decorators": []
    },
    {
     "name": "hostnames",
     "kind": "function",
     "signature": "() -> list[str]",
     "doc": "",
     "line": 37,
     "decorators": []
    },
    {
     "name": "ssh",
     "kind": "function",
     "signature": "(vm: Vm, command: str, timeout: float=30) -> subprocess.CompletedProcess",
     "doc": "Run ``command`` on ``vm``. Never raises on failure or timeout; check ``returncode``.",
     "line": 41,
     "decorators": []
    }
   ]
  },
  {
   "path": "pipeline/run_all.py",
   "module": "pipeline.run_all",
   "doc": "Run the whole two-engine experiment as one terminal UI.\n\nFor each engine in turn -- CockroachDB, then PostgreSQL/Patroni:\n\n    terraform plan -out plan.out -var=database_engine=<engine>\n    terraform apply plan.out                 (any error aborts)\n    wait for all six VMs to finish cloud-init\n    ./run-experiment.sh --engine <engine> --profile <profile>\n    tailscale logout on every VM\n    terraform destroy -auto-approve\n    delete the VMs' devices from the tailnet (Tailscale API), and verify\n\nand then ``./generate_insights.sh --profile <profile>`` plus the engine\ncomparison. DB_URI needs no editing between engines: run-experiment.sh derives\nit from --engine.\n\nIf a step fails, or you press Ctrl-C, while VMs may be up, it asks whether to\ndestroy them (and clean the tailnet) or leave them up for debugging.\n\n    ./run-experiment.sh                       # this, from a terminal\n    pipeline/run_all.py --profile smoke --yes # no setup form\n    pipeline/run_all.py --engines postgresql  # resume with the second engine\n    pipeline/run_all.py --cleanup --engine cockroachdb\n    pipeline/run_all.py --tailscale-list      # which devices would be deleted\n    pipeline/run_all.py --dry-run             # the UI, with no cloud calls\n\nA full thesis-extended run takes hours; start it inside tmux so it survives a\nclosed terminal. Everything is also written to runs/_logs/pipeline-<stamp>.log.",
   "lines": 1107,
   "constants": [
    {
     "name": "REPO",
     "value": "Path(__file__).resolve().parent.parent",
     "doc": "",
     "line": 48
    },
    {
     "name": "TF_DIR",
     "value": "REPO / 'terraform'",
     "doc": "",
     "line": 63
    },
    {
     "name": "RUNS",
     "value": "REPO / 'runs'",
     "doc": "",
     "line": 64
    },
    {
     "name": "LOGS",
     "value": "RUNS / '_logs'",
     "doc": "",
     "line": 65
    },
    {
     "name": "PROFILES",
     "value": "REPO / 'profiles'",
     "doc": "",
     "line": 66
    },
    {
     "name": "ENGINES",
     "value": "('cockroachdb', 'postgresql')",
     "doc": "",
     "line": 67
    },
    {
     "name": "ENGINE_NAME",
     "value": "{'cockroachdb': 'CockroachDB', 'postgresql': 'PostgreSQL/Patroni'}",
     "doc": "",
     "line": 68
    },
    {
     "name": "NODE_WAIT_S",
     "value": "15 * 60",
     "doc": "cloud-init on the slowest cloud has taken ~8 minutes; this bounds the wait rather than letting a VM that never boots hang the pipeline.",
     "line": 72
    },
    {
     "name": "STOP_GRACE_S",
     "value": "90",
     "doc": "How long a child gets to stop after SIGINT before it is terminated. Terraform uses SIGINT to stop gracefully (and to cancel a pending remote run).",
     "line": 76
    },
    {
     "name": "NON_SGR",
     "value": "re.compile('\\\\x1b\\\\[[0-9;?]*[A-La-ln-z]|\\\\x1b\\\\][^\\\\x07]*\\\\x07|\\\\x1b[()][A-Z0-9]')",
     "doc": "",
     "line": 80
    },
    {
     "name": "ANY_ANSI",
     "value": "re.compile('\\\\x1b\\\\[[0-9;?]*[A-Za-z]')",
     "doc": "",
     "line": 81
    },
    {
     "name": "TF_DONE",
     "value": "re.compile('\\\\.([a-z0-9]+_[a-z0-9_]+)\\\\.[^.:\\\\s]+: (Creation|Destruction) complete')",
     "doc": "",
     "line": 82
    },
    {
     "name": "SUBSTAGE_OF",
     "value": "[('Checking the workstation', 'checks'), ('Resolving topology', 'checks'), ('Checking the testbed', 'testbed'), ('Working set', 'load'), ('Phase IV', 'p4'), ...",
     "doc": "run-experiment.sh's `==> <step>` headers -> substage. Most specific first.",
     "line": 85
    },
    {
     "name": "SUBSTAGES",
     "value": "[('checks', 'workstation & topology'), ('testbed', 'testbed health'), ('load', 'working set'), ('p1', 'Phase I   network'), ('p2', 'Phase II  benchmark'), ('...",
     "doc": "",
     "line": 92
    }
   ],
   "classes": [
    {
     "name": "StepFailed",
     "kind": "class",
     "bases": [
      "Exception"
     ],
     "decorators": [],
     "doc": "",
     "line": 100,
     "fields": [],
     "methods": []
    },
    {
     "name": "Aborted",
     "kind": "class",
     "bases": [
      "Exception"
     ],
     "decorators": [],
     "doc": "",
     "line": 104,
     "fields": [],
     "methods": []
    },
    {
     "name": "Step",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "",
     "line": 125,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, key: str, title: str, engine: str='')",
       "doc": "",
       "line": 126,
       "decorators": []
      },
      {
       "name": "elapsed",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "",
       "line": 132,
       "decorators": [
        "property"
       ]
      }
     ]
    },
    {
     "name": "State",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "",
     "line": 138,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, args: argparse.Namespace)",
       "doc": "",
       "line": 139,
       "decorators": []
      },
      {
       "name": "step",
       "kind": "function",
       "signature": "(self, key: str) -> Step",
       "doc": "",
       "line": 168,
       "decorators": []
      },
      {
       "name": "active",
       "kind": "function",
       "signature": "(self) -> Step | None",
       "doc": "",
       "line": 172,
       "decorators": [
        "property"
       ]
      },
      {
       "name": "emit",
       "kind": "function",
       "signature": "(self, text: str) -> None",
       "doc": "",
       "line": 175,
       "decorators": []
      }
     ]
    },
    {
     "name": "Runner",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "",
     "line": 186,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, state: State, ui: BaseUI)",
       "doc": "",
       "line": 187,
       "decorators": []
      },
      {
       "name": "say",
       "kind": "function",
       "signature": "(self, text: str, colour: str='') -> None",
       "doc": "",
       "line": 196,
       "decorators": []
      },
      {
       "name": "check_abort",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 200,
       "decorators": []
      },
      {
       "name": "cmd",
       "kind": "function",
       "signature": "(self, argv: list[str], cwd: Path=REPO, fake: list[str] | None=None) -> None",
       "doc": "Run ``argv``, streaming its output into the log; raise StepFailed on non-zero.",
       "line": 204,
       "decorators": []
      },
      {
       "name": "_stop_on_abort",
       "kind": "function",
       "signature": "(self, proc: subprocess.Popen) -> None",
       "doc": "",
       "line": 239,
       "decorators": []
      },
      {
       "name": "_line",
       "kind": "function",
       "signature": "(self, text: str) -> None",
       "doc": "",
       "line": 253,
       "decorators": []
      },
      {
       "name": "_fake",
       "kind": "function",
       "signature": "(self, argv: list[str], lines: list[str]) -> None",
       "doc": "",
       "line": 273,
       "decorators": []
      },
      {
       "name": "begin",
       "kind": "function",
       "signature": "(self, key: str) -> Step",
       "doc": "",
       "line": 282,
       "decorators": []
      },
      {
       "name": "end",
       "kind": "function",
       "signature": "(self, step: Step, status: str='done') -> None",
       "doc": "",
       "line": 294,
       "decorators": []
      },
      {
       "name": "run",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 299,
       "decorators": []
      },
      {
       "name": "preflight",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 314,
       "decorators": []
      },
      {
       "name": "one_engine",
       "kind": "function",
       "signature": "(self, e: str) -> None",
       "doc": "",
       "line": 334,
       "decorators": []
      },
      {
       "name": "teardown",
       "kind": "function",
       "signature": "(self, e: str) -> None",
       "doc": "",
       "line": 374,
       "decorators": []
      },
      {
       "name": "logout",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 388,
       "decorators": []
      },
      {
       "name": "destroy",
       "kind": "function",
       "signature": "(self, e: str) -> None",
       "doc": "",
       "line": 395,
       "decorators": []
      },
      {
       "name": "purge",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 404,
       "decorators": []
      },
      {
       "name": "wait_for_vms",
       "kind": "function",
       "signature": "(self, e: str) -> None",
       "doc": "",
       "line": 411,
       "decorators": []
      },
      {
       "name": "insights",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 456,
       "decorators": []
      },
      {
       "name": "fail",
       "kind": "function",
       "signature": "(self, err: Exception) -> None",
       "doc": "",
       "line": 482,
       "decorators": []
      },
      {
       "name": "cleanup",
       "kind": "function",
       "signature": "(self, engine: str) -> None",
       "doc": "",
       "line": 512,
       "decorators": []
      }
     ]
    },
    {
     "name": "BaseUI",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "",
     "line": 567,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, state: State)",
       "doc": "",
       "line": 568,
       "decorators": []
      },
      {
       "name": "ask",
       "kind": "function",
       "signature": "(self, title: str, lines: list[str], options: dict[str, str]) -> str",
       "doc": "",
       "line": 571,
       "decorators": []
      }
     ]
    },
    {
     "name": "PlainUI",
     "kind": "class",
     "bases": [
      "BaseUI"
     ],
     "decorators": [],
     "doc": "Scrolling output, for a pipe, a small terminal, or --plain.",
     "line": 575,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, state: State)",
       "doc": "",
       "line": 578,
       "decorators": []
      },
      {
       "name": "pump",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 584,
       "decorators": []
      },
      {
       "name": "ask",
       "kind": "function",
       "signature": "(self, title, lines, options)",
       "doc": "",
       "line": 591,
       "decorators": []
      }
     ]
    },
    {
     "name": "TUI",
     "kind": "class",
     "bases": [
      "BaseUI"
     ],
     "decorators": [],
     "doc": "The full-screen UI: reuses demo/replay.py's Screen for colours and drawing.",
     "line": 608,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, state: State, scr, ascii_only: bool)",
       "doc": "",
       "line": 611,
       "decorators": []
      },
      {
       "name": "ask",
       "kind": "function",
       "signature": "(self, title, lines, options)",
       "doc": "",
       "line": 619,
       "decorators": []
      },
      {
       "name": "loop",
       "kind": "function",
       "signature": "(self, runner_thread: threading.Thread) -> None",
       "doc": "",
       "line": 626,
       "decorators": []
      },
      {
       "name": "interrupt",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 641,
       "decorators": []
      },
      {
       "name": "keys",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 646,
       "decorators": []
      },
      {
       "name": "finale",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 671,
       "decorators": []
      }
     ]
    },
    {
     "name": "_View",
     "kind": "class",
     "bases": [
      "Screen"
     ],
     "decorators": [],
     "doc": "Draws live pipeline state with replay.Screen's colours and primitives.\n\nOnly ``put``, ``bar``, ``attr`` and ``_colours`` are inherited; the playback\nmachinery of Screen is not used, so its constructor is not either.",
     "line": 694,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, scr, ascii_only: bool)",
       "doc": "",
       "line": 701,
       "decorators": []
      },
      {
       "name": "draw",
       "kind": "function",
       "signature": "(self, s: State, scroll: int, question: tuple | None, done: bool=False) -> None",
       "doc": "",
       "line": 706,
       "decorators": []
      },
      {
       "name": "_top",
       "kind": "function",
       "signature": "(self, s: State, w: int) -> None",
       "doc": "",
       "line": 725,
       "decorators": []
      },
      {
       "name": "_steps",
       "kind": "function",
       "signature": "(self, s: State, y0: int, x0: int, width: int, height: int) -> None",
       "doc": "",
       "line": 737,
       "decorators": []
      },
      {
       "name": "_step_row",
       "kind": "function",
       "signature": "(self, s: State, st: Step, y: int, x: int, width: int) -> int",
       "doc": "",
       "line": 774,
       "decorators": []
      },
      {
       "name": "_log",
       "kind": "function",
       "signature": "(self, s: State, y0: int, x0: int, width: int, height: int, scroll: int) -> None",
       "doc": "",
       "line": 790,
       "decorators": []
      },
      {
       "name": "_live",
       "kind": "function",
       "signature": "(self, s: State, y0: int, x0: int, width: int, height: int, done: bool) -> None",
       "doc": "",
       "line": 819,
       "decorators": []
      },
      {
       "name": "_summary",
       "kind": "function",
       "signature": "(self, s: State, y0: int, x0: int) -> None",
       "doc": "",
       "line": 862,
       "decorators": []
      },
      {
       "name": "_modal",
       "kind": "function",
       "signature": "(self, q: tuple, h: int, w: int) -> None",
       "doc": "",
       "line": 881,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "utc_stamp",
     "kind": "function",
     "signature": "() -> str",
     "doc": "",
     "line": 108,
     "decorators": []
    },
    {
     "name": "fmt_dur",
     "kind": "function",
     "signature": "(seconds: float) -> str",
     "doc": "",
     "line": 112,
     "decorators": []
    },
    {
     "name": "profiles",
     "kind": "function",
     "signature": "() -> list[str]",
     "doc": "",
     "line": 117,
     "decorators": []
    },
    {
     "name": "_which",
     "kind": "function",
     "signature": "(tool: str) -> bool",
     "doc": "",
     "line": 531,
     "decorators": []
    },
    {
     "name": "_fake_plan",
     "kind": "function",
     "signature": "(e: str) -> list[str]",
     "doc": "",
     "line": 537,
     "decorators": []
    },
    {
     "name": "_fake_tf",
     "kind": "function",
     "signature": "(action: str) -> list[str]",
     "doc": "",
     "line": 544,
     "decorators": []
    },
    {
     "name": "_fake_run",
     "kind": "function",
     "signature": "(chaos: bool) -> list[str]",
     "doc": "",
     "line": 553,
     "decorators": []
    },
    {
     "name": "choose_curses",
     "kind": "function",
     "signature": "(scr, title: str, options: list[tuple[str, str]], default: int, body: list[str] | None=None) -> int",
     "doc": "Arrow-key menu; returns the chosen index. q quits.",
     "line": 895,
     "decorators": []
    },
    {
     "name": "plan_lines",
     "kind": "function",
     "signature": "(args) -> list[str]",
     "doc": "",
     "line": 928,
     "decorators": []
    },
    {
     "name": "setup_curses",
     "kind": "function",
     "signature": "(scr, args) -> None",
     "doc": "",
     "line": 943,
     "decorators": []
    },
    {
     "name": "setup_plain",
     "kind": "function",
     "signature": "(args) -> None",
     "doc": "",
     "line": 966,
     "decorators": []
    },
    {
     "name": "parse_args",
     "kind": "function",
     "signature": "(argv)",
     "doc": "",
     "line": 986,
     "decorators": []
    },
    {
     "name": "main",
     "kind": "function",
     "signature": "(argv: list[str] | None=None) -> int",
     "doc": "",
     "line": 1024,
     "decorators": []
    },
    {
     "name": "_main",
     "kind": "function",
     "signature": "(argv: list[str] | None=None) -> int",
     "doc": "",
     "line": 1032,
     "decorators": []
    }
   ]
  },
  {
   "path": "pipeline/tailscale.py",
   "module": "pipeline.tailscale",
   "doc": "Remove the testbed's devices from the tailnet, so a redeploy gets its names back.\n\nTerraform destroys the VMs but not their Tailscale registrations, so the next\ndeploy's hostnames would get a ``-1`` suffix and MagicDNS would resolve dead\nmachines. Two layers:\n\n* :func:`logout_vms`: before destroy, each reachable VM logs itself out.\n* :func:`purge_devices`: after destroy, the Tailscale API deletes every device\n  with a testbed hostname, then verifies none remain. This step fails loudly.\n\nNeeds ``TS_API_KEY`` and optionally ``TS_TAILNET`` (default ``-``).",
   "lines": 167,
   "constants": [
    {
     "name": "API",
     "value": "'https://api.tailscale.com/api/v2'",
     "doc": "",
     "line": 27
    }
   ],
   "classes": [
    {
     "name": "TailscaleError",
     "kind": "class",
     "bases": [
      "RuntimeError"
     ],
     "decorators": [],
     "doc": "",
     "line": 32,
     "fields": [],
     "methods": []
    }
   ],
   "functions": [
    {
     "name": "_credentials",
     "kind": "function",
     "signature": "() -> tuple[str, str]",
     "doc": "",
     "line": 36,
     "decorators": []
    },
    {
     "name": "_request",
     "kind": "function",
     "signature": "(method: str, path: str) -> dict",
     "doc": "",
     "line": 46,
     "decorators": []
    },
    {
     "name": "list_devices",
     "kind": "function",
     "signature": "() -> list[dict]",
     "doc": "",
     "line": 61,
     "decorators": []
    },
    {
     "name": "_self_name",
     "kind": "function",
     "signature": "() -> str",
     "doc": "This workstation's MagicDNS name, so it can never be matched by accident.",
     "line": 66,
     "decorators": []
    },
    {
     "name": "matching",
     "kind": "function",
     "signature": "(devices: Iterable[dict], hosts: Iterable[str], exclude_name: str='') -> list[dict]",
     "doc": "Devices that belong to the testbed.\n\nA device matches if its hostname is exactly one of ``hosts``, or its\nMagicDNS name's first label is one of ``hosts`` with an optional ``-<n>``\nsuffix -- the form Tailscale gives a device whose name was already taken,\ne.g. ``crdb-gcp-1-1``. Nothing broader: ``crdb-gcp-10`` is not ``crdb-gcp-1``.",
     "line": 76,
     "decorators": []
    },
    {
     "name": "testbed_devices",
     "kind": "function",
     "signature": "() -> list[dict]",
     "doc": "",
     "line": 97,
     "decorators": []
    },
    {
     "name": "describe",
     "kind": "function",
     "signature": "(d: dict) -> str",
     "doc": "",
     "line": 101,
     "decorators": []
    },
    {
     "name": "purge_devices",
     "kind": "function",
     "signature": "(log: Log=print) -> int",
     "doc": "Delete every testbed device from the tailnet and verify. Returns how many.",
     "line": 106,
     "decorators": []
    },
    {
     "name": "logout_vms",
     "kind": "function",
     "signature": "(log: Log=print) -> None",
     "doc": "Have every reachable VM log itself out of the tailnet. Never raises.\n\nThe logout is detached and delayed by a second because the SSH session runs\nover Tailscale itself: logging out inline would cut the connection that is\nwaiting for the command to return.",
     "line": 123,
     "decorators": []
    },
    {
     "name": "main",
     "kind": "function",
     "signature": "(argv: list[str] | None=None) -> int",
     "doc": "``python -m pipeline.tailscale [list|purge]`` -- for use outside the TUI.",
     "line": 142,
     "decorators": []
    }
   ]
  },
  {
   "path": "demo/replay.py",
   "module": "demo.replay",
   "doc": "Replay the recorded thesis-extended experiment as a terminal UI, in minutes.\n\nThe full two-engine workflow (``./run-experiment.sh``) takes most of seven\nhours. This plays back what actually happened, compressed in time:\n\n* Experiment output comes from the recorded thesis-extended logs in\n  ``runs/_logs``; throughput graphs come from each run's ``metrics.csv``.\n* Terraform, VM waits and Tailscale cleanup come from a recorded pipeline run\n  (``PIPELINE_LOG``), with account identifiers masked.\n* The closing insights are the recorded render and engine comparison.\n* A few splices fill gaps from the same profile and deployment (a re-run\n  Phase IV, a reused data load, a rebuilt summary). ``--as-recorded`` skips them.\n\nUsage (from the repository root)::\n\n    ./demo.sh                       # ~4 minutes, full-screen\n    ./demo.sh --minutes 3           # tighter\n    ./demo.sh --plain               # plain scrolling output\n\nKeys: space pause - n skip to the next step - + / - speed - q quit.",
   "lines": 1195,
   "constants": [
    {
     "name": "REPO",
     "value": "Path(__file__).resolve().parent.parent",
     "doc": "",
     "line": 38
    },
    {
     "name": "RUNS",
     "value": "REPO / 'runs'",
     "doc": "",
     "line": 39
    },
    {
     "name": "LOGS",
     "value": "RUNS / '_logs'",
     "doc": "",
     "line": 40
    },
    {
     "name": "TERRAFORM",
     "value": "REPO / 'terraform'",
     "doc": "",
     "line": 41
    },
    {
     "name": "CRDB_LOG",
     "value": "'experiment-20260911T164025Z.log'",
     "doc": "",
     "line": 43
    },
    {
     "name": "CRDB_P4_LOG",
     "value": "'chaos-dead-resume-20260911T182028Z.log'",
     "doc": "",
     "line": 44
    },
    {
     "name": "PG_LOG",
     "value": "'experiment-20260911T084546Z.log'",
     "doc": "",
     "line": 45
    },
    {
     "name": "PG_LOAD_LOG",
     "value": "'experiment-20260911T052424Z.log'",
     "doc": "",
     "line": 46
    },
    {
     "name": "PIPELINE_LOG",
     "value": "'pipeline-20260929T134252Z.log'",
     "doc": "Recorded pipeline run supplying terraform, VM-wait and tailscale lines (terraform output does not depend on the profile).",
     "line": 49
    },
    {
     "name": "INSIGHTS_LOG",
     "value": "'insights-20260928T143346Z.log'",
     "doc": "`generate_insights.sh --profile thesis-extended` over the runs above.",
     "line": 51
    },
    {
     "name": "COMPARISON_LOG",
     "value": "'engine-comparison-20260929T151755Z.log'",
     "doc": "`crdblab analyze engine-comparison` on the two thesis-extended bench runs.",
     "line": 53
    },
    {
     "name": "ANSI",
     "value": "re.compile('\\\\x1b\\\\[([0-9;]*)m')",
     "doc": "",
     "line": 55
    },
    {
     "name": "PROFILE",
     "value": "'thesis-extended'",
     "doc": "",
     "line": 375
    },
    {
     "name": "ENGINE_OF",
     "value": "{'CockroachDB': 'cockroachdb', 'PostgreSQL/Patroni': 'postgresql'}",
     "doc": "",
     "line": 376
    },
    {
     "name": "PREFIX",
     "value": "{'cockroachdb': 'crdb', 'postgresql': 'pg'}",
     "doc": "",
     "line": 377
    },
    {
     "name": "STEP_OF",
     "value": "{'preflight': 'pre', 'terraform plan': 'plan', 'terraform apply': 'apply', 'wait for 6 VMs': 'wait', './run-experiment.sh': 'run', 'tailscale logout': 'logou...",
     "doc": "",
     "line": 378
    },
    {
     "name": "HEADER",
     "value": "re.compile('^\u2501\u2501 (.+?)(?:  \u00b7  (.+))?$')",
     "doc": "",
     "line": 382
    },
    {
     "name": "TF_DONE",
     "value": "re.compile('\\\\.([a-z0-9]+_[a-z0-9_]+)\\\\.[^.:\\\\s]+: (Creation|Destruction) complete')",
     "doc": "",
     "line": 383
    },
    {
     "name": "TF_TICK",
     "value": "re.compile(': (Still (creating|destroying)\\\\.\\\\.\\\\. \\\\[|(Creation|Destruction) complete after)')",
     "doc": "",
     "line": 384
    },
    {
     "name": "OK_DUR",
     "value": "re.compile('^  ok  .+\\\\((\\\\d+m\\\\d\\\\ds|\\\\d+h\\\\d\\\\dm)\\\\)$')",
     "doc": "",
     "line": 385
    },
    {
     "name": "MASKS",
     "value": "[(re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'), '<subscription>'), (re.compile('project-[0-9a-f]{8}-[0-9a-f-]+'), '<gcp-project...",
     "doc": "Masks for account ids, tailnet identity and home directory; nothing else changes.",
     "line": 388
    },
    {
     "name": "PIPE_STEPS",
     "value": "[('plan', 'terraform plan'), ('apply', 'terraform apply'), ('wait', 'wait for 6 VMs'), ('run', './run-experiment.sh'), ('logout', 'tailscale logout'), ('dest...",
     "doc": "The pipeline's steps, as pipeline/run_all.py names them.",
     "line": 537
    },
    {
     "name": "GROUPS",
     "value": "[('pre', 'preflight', [])] + [(p, name, [f'{p}:{k}' for k, _ in PIPE_STEPS]) for p, name in (('crdb', 'CockroachDB'), ('pg', 'PostgreSQL/Patroni'))] + [('ins...",
     "doc": "",
     "line": 541
    },
    {
     "name": "TOP_STEPS",
     "value": "{'pre', 'insights'} | {s for _, _, keys in GROUPS for s in keys}",
     "doc": "",
     "line": 545
    },
    {
     "name": "SUBSTAGES",
     "value": "[('checks', 'workstation & topology'), ('testbed', 'testbed health'), ('load', 'working set, 7.5M rows'), ('p1', 'Phase I   network'), ('p2', 'Phase II  benc...",
     "doc": "",
     "line": 546
    },
    {
     "name": "SUB_IDS",
     "value": "{s for s, _ in SUBSTAGES}",
     "doc": "",
     "line": 552
    },
    {
     "name": "BUDGET",
     "value": "{'intro': 0.03, 'pre': 0.01, 'insights': 0.04}",
     "doc": "Share of the playback each section gets. Long recorded phases are compressed harder than short ones; the shares were chosen so every phase is legible.",
     "line": 556
    },
    {
     "name": "SPARK",
     "value": "' \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588'",
     "doc": "",
     "line": 597
    },
    {
     "name": "GLYPHS",
     "value": "{'done': '\u2713', 'active': '\u25b6', 'sub': '\u25b8', 'pending': '\u00b7', 'vline': '\u2502', 'hline': '\u2500', 'prompt': '\u276f', 'fault': '\u258c'}",
     "doc": "Non-ASCII glyphs the UI draws; ``--ascii`` swaps them for plain ASCII.",
     "line": 600
    },
    {
     "name": "ASCII_GLYPHS",
     "value": "{'done': '+', 'active': '>', 'sub': '>', 'pending': '-', 'vline': '|', 'hline': '-', 'prompt': '$', 'fault': '|'}",
     "doc": "",
     "line": 602
    }
   ],
   "classes": [
    {
     "name": "Event",
     "kind": "class",
     "bases": [],
     "decorators": [
      "dataclass"
     ],
     "doc": "",
     "line": 61,
     "fields": [
      {
       "name": "kind",
       "type": "str",
       "default": "",
       "doc": ""
      },
      {
       "name": "text",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "real_s",
       "type": "float",
       "default": "0.0",
       "doc": ""
      },
      {
       "name": "section",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "stage",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "bulk",
       "type": "bool",
       "default": "False",
       "doc": ""
      },
      {
       "name": "dense",
       "type": "bool",
       "default": "False",
       "doc": ""
      },
      {
       "name": "label",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "series",
       "type": "list",
       "default": "field(default_factory=list)",
       "doc": ""
      },
      {
       "name": "marks",
       "type": "list",
       "default": "field(default_factory=list)",
       "doc": ""
      },
      {
       "name": "fault_at",
       "type": "float | None",
       "default": "None",
       "doc": ""
      },
      {
       "name": "engine",
       "type": "str",
       "default": "''",
       "doc": ""
      },
      {
       "name": "tf",
       "type": "dict",
       "default": "field(default_factory=dict)",
       "doc": ""
      },
      {
       "name": "playback",
       "type": "float",
       "default": "0.0",
       "doc": ""
      }
     ],
     "methods": []
    },
    {
     "name": "Screen",
     "kind": "class",
     "bases": [],
     "decorators": [],
     "doc": "",
     "line": 627,
     "fields": [],
     "methods": [
      {
       "name": "__init__",
       "kind": "function",
       "signature": "(self, scr, events: list[Event], total_s: float, captions: bool, ascii_only: bool=False)",
       "doc": "",
       "line": 628,
       "decorators": []
      },
      {
       "name": "_colours",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 659,
       "decorators": []
      },
      {
       "name": "attr",
       "kind": "function",
       "signature": "(self, style: tuple) -> int",
       "doc": "",
       "line": 686,
       "decorators": []
      },
      {
       "name": "bar",
       "kind": "function",
       "signature": "(self, y: int, x: int, width: int, fraction: float, colour: str) -> None",
       "doc": "A progress bar drawn as coloured cells, with no glyph a font could lack.",
       "line": 695,
       "decorators": []
      },
      {
       "name": "put",
       "kind": "function",
       "signature": "(self, y: int, x: int, text: str, attr: int=0) -> None",
       "doc": "",
       "line": 701,
       "decorators": []
      },
      {
       "name": "draw",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 711,
       "decorators": []
      },
      {
       "name": "_title",
       "kind": "function",
       "signature": "(self, w: int) -> None",
       "doc": "",
       "line": 724,
       "decorators": []
      },
      {
       "name": "elapsed",
       "kind": "function",
       "signature": "(self) -> float",
       "doc": "",
       "line": 734,
       "decorators": []
      },
      {
       "name": "_checklist",
       "kind": "function",
       "signature": "(self, y0: int, x0: int, width: int, height: int) -> None",
       "doc": "The pipeline's steps, grouped by engine, as pipeline/run_all.py draws them.\n\nAn engine's group is collapsed to one line until it starts and once it\nhas finished, so the whole pipeline fits a 28-row terminal.",
       "line": 737,
       "decorators": []
      },
      {
       "name": "_step_row",
       "kind": "function",
       "signature": "(self, key: str, title: str, y: int, x: int, width: int) -> int",
       "doc": "",
       "line": 779,
       "decorators": []
      },
      {
       "name": "_took",
       "kind": "function",
       "signature": "(self, y: int, seconds: float, width: int) -> None",
       "doc": "",
       "line": 793,
       "decorators": []
      },
      {
       "name": "_terminal",
       "kind": "function",
       "signature": "(self, y0: int, x0: int, width: int, height: int) -> None",
       "doc": "",
       "line": 798,
       "decorators": []
      },
      {
       "name": "_panel",
       "kind": "function",
       "signature": "(self, y0: int, x0: int, width: int, height: int) -> None",
       "doc": "",
       "line": 825,
       "decorators": []
      },
      {
       "name": "_help",
       "kind": "function",
       "signature": "(self, y: int, w: int) -> None",
       "doc": "",
       "line": 889,
       "decorators": []
      },
      {
       "name": "keys",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 894,
       "decorators": []
      },
      {
       "name": "sleep",
       "kind": "function",
       "signature": "(self, seconds: float, frame=None) -> None",
       "doc": "Advance the schedule by ``seconds`` of playback, drawing as it goes.\n\nPlayback runs on a virtual clock; time spent drawing is absorbed by the\nnext wait, so the replay lasts exactly as long as scheduled.",
       "line": 918,
       "decorators": []
      },
      {
       "name": "run",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 944,
       "decorators": []
      },
      {
       "name": "_apply_instant",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 953,
       "decorators": []
      },
      {
       "name": "_stage",
       "kind": "function",
       "signature": "(self, stage: str) -> None",
       "doc": "",
       "line": 966,
       "decorators": []
      },
      {
       "name": "_finish_step",
       "kind": "function",
       "signature": "(self) -> None",
       "doc": "",
       "line": 994,
       "decorators": []
      },
      {
       "name": "_tf_count",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1002,
       "decorators": []
      },
      {
       "name": "_prompt",
       "kind": "function",
       "signature": "(self, e: Event) -> str",
       "doc": "",
       "line": 1007,
       "decorators": []
      },
      {
       "name": "_play_card",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1010,
       "decorators": []
      },
      {
       "name": "_play_stage",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1032,
       "decorators": []
      },
      {
       "name": "_play_cmd",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1037,
       "decorators": []
      },
      {
       "name": "_play_line",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1052,
       "decorators": []
      },
      {
       "name": "_play_wait",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1065,
       "decorators": []
      },
      {
       "name": "_play_anim",
       "kind": "function",
       "signature": "(self, e: Event) -> None",
       "doc": "",
       "line": 1077,
       "decorators": []
      }
     ]
    }
   ],
   "functions": [
    {
     "name": "_dur",
     "kind": "function",
     "signature": "(text: str) -> float",
     "doc": "``1h 2m 3s`` / ``16m 56s`` / ``45.5s`` -> seconds.",
     "line": 78,
     "decorators": []
    },
    {
     "name": "_plain",
     "kind": "function",
     "signature": "(text: str) -> str",
     "doc": "",
     "line": 86,
     "decorators": []
    },
    {
     "name": "read_log",
     "kind": "function",
     "signature": "(name: str) -> list[str]",
     "doc": "",
     "line": 90,
     "decorators": []
    },
    {
     "name": "_ticks",
     "kind": "function",
     "signature": "(run_id: str) -> dict",
     "doc": "``{(concurrency, repetition): [(elapsed, wall, tps)]}``, tps summed across ops.",
     "line": 96,
     "decorators": []
    },
    {
     "name": "tier_series",
     "kind": "function",
     "signature": "(run_id: str, concurrency: int, rep: int) -> list",
     "doc": "",
     "line": 117,
     "decorators": []
    },
    {
     "name": "chaos_series",
     "kind": "function",
     "signature": "(run_id: str) -> list",
     "doc": "",
     "line": 122,
     "decorators": []
    },
    {
     "name": "_section_for",
     "kind": "function",
     "signature": "(header: str, engine: str) -> tuple[str, str]",
     "doc": "",
     "line": 129,
     "decorators": []
    },
    {
     "name": "parse_run_log",
     "kind": "function",
     "signature": "(lines: list[str], engine: str) -> list[Event]",
     "doc": "One run-experiment.sh log as timed events, with real data behind every wait.",
     "line": 145,
     "decorators": []
    },
    {
     "name": "_manifest",
     "kind": "function",
     "signature": "(run_id: str) -> dict",
     "doc": "",
     "line": 252,
     "decorators": []
    },
    {
     "name": "_utc",
     "kind": "function",
     "signature": "(stamp: str | None) -> datetime | None",
     "doc": "",
     "line": 259,
     "decorators": []
    },
    {
     "name": "_fmt_dur",
     "kind": "function",
     "signature": "(seconds: float) -> str",
     "doc": "",
     "line": 263,
     "decorators": []
    },
    {
     "name": "crdb_lines",
     "kind": "function",
     "signature": "(as_recorded: bool) -> list[str]",
     "doc": "",
     "line": 268,
     "decorators": []
    },
    {
     "name": "crdb_headlines",
     "kind": "function",
     "signature": "(main: list[str], resume: list[str]) -> list[str]",
     "doc": "",
     "line": 297,
     "decorators": []
    },
    {
     "name": "pg_lines",
     "kind": "function",
     "signature": "(as_recorded: bool) -> list[str]",
     "doc": "",
     "line": 337,
     "decorators": []
    },
    {
     "name": "tf_resources",
     "kind": "function",
     "signature": "() -> list[tuple[str, str]]",
     "doc": "``(address, type)`` for every resource the root module would create.",
     "line": 357,
     "decorators": []
    },
    {
     "name": "_provider",
     "kind": "function",
     "signature": "(rtype: str) -> str",
     "doc": "",
     "line": 371,
     "decorators": []
    },
    {
     "name": "_mask",
     "kind": "function",
     "signature": "(text: str) -> str",
     "doc": "",
     "line": 396,
     "decorators": []
    },
    {
     "name": "_style",
     "kind": "function",
     "signature": "(text: str) -> str",
     "doc": "Re-colour a line of the (ANSI-stripped) pipeline log the way the TUI drew it.",
     "line": 402,
     "decorators": []
    },
    {
     "name": "pipeline_segments",
     "kind": "function",
     "signature": "() -> list[dict]",
     "doc": "The recorded pipeline log split at its ``\u2501\u2501 <step>  \u00b7  <engine>`` headers.",
     "line": 417,
     "decorators": []
    },
    {
     "name": "segment_events",
     "kind": "function",
     "signature": "(seg: dict, section: str) -> list[Event]",
     "doc": "One recorded pipeline step as events, its recorded duration spread over its ticks.",
     "line": 439,
     "decorators": []
    },
    {
     "name": "insights_lines",
     "kind": "function",
     "signature": "() -> list[str]",
     "doc": "",
     "line": 480,
     "decorators": []
    },
    {
     "name": "storyboard",
     "kind": "function",
     "signature": "(as_recorded: bool) -> list[Event]",
     "doc": "",
     "line": 487,
     "decorators": []
    },
    {
     "name": "schedule",
     "kind": "function",
     "signature": "(events: list[Event], total_s: float) -> None",
     "doc": "Give every event a playback duration so the whole thing lasts ``total_s``.",
     "line": 566,
     "decorators": []
    },
    {
     "name": "segments",
     "kind": "function",
     "signature": "(raw: str) -> list[tuple[str, tuple]]",
     "doc": "ANSI SGR text -> [(text, (bold, dim, colour))].",
     "line": 606,
     "decorators": []
    },
    {
     "name": "play_plain",
     "kind": "function",
     "signature": "(events: list[Event], speed: float) -> None",
     "doc": "",
     "line": 1101,
     "decorators": []
    },
    {
     "name": "main",
     "kind": "function",
     "signature": "(argv: list[str] | None=None) -> int",
     "doc": "",
     "line": 1139,
     "decorators": []
    }
   ]
  }
 ],
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
 },
 "tests": [
  {
   "path": "tests/test_analysis.py",
   "doc": "Tests for the analysis layer: loading, steady state, engine comparison and resilience.",
   "tests": [
    {
     "name": "test_a_run_without_a_manifest_is_not_loadable",
     "doc": "Provenance is a precondition of analysis, not an optional extra."
    },
    {
     "name": "test_a_run_that_fails_validation_is_refused",
     "doc": "Validation gates analysis; it is not advisory."
    },
    {
     "name": "test_throughput_sums_across_op_types_but_errors_do_not",
     "doc": "Read and write rates are components of one load, so throughput sums across ops."
    },
    {
     "name": "test_latency_is_never_pooled_across_operation_types",
     "doc": "The op type stays a grouping key, so pooling is structurally impossible."
    },
    {
     "name": "test_a_single_repetition_yields_no_interval",
     "doc": "An unmeasured spread is None, never zero."
    },
    {
     "name": "test_the_interval_is_computed_over_repetitions_not_pooled_samples",
     "doc": "Successive seconds of one run are not independent observations."
    },
    {
     "name": "test_matched_throughput_refuses_when_the_ranges_do_not_overlap",
     "doc": "No load level was measured for both engines, so no matched-throughput scalar is produced."
    },
    {
     "name": "test_matched_throughput_compares_only_inside_the_measured_range",
     "doc": "Where the ranges do overlap, every point lies within both curves."
    },
    {
     "name": "test_the_same_concurrency_delta_is_produced_only_as_a_labelled_artefact",
     "doc": "The intuitive comparison is computed, and marked as not a result."
    },
    {
     "name": "test_a_comparison_across_mismatched_block_caches_is_refused",
     "doc": "Mismatched block caches make a comparison invalid; no single-run check can detect it."
    },
    {
     "name": "test_a_comparison_across_different_workloads_is_refused",
     "doc": "A seed mismatch means the two runs did different work."
    },
    {
     "name": "test_compare_refuses_outright_when_the_runs_are_not_comparable",
     "doc": ""
    },
    {
     "name": "test_a_still_rising_curve_reports_its_peak_as_a_lower_bound",
     "doc": "A throughput that is still climbing is a lower bound, not the capacity."
    },
    {
     "name": "test_alignment_is_measured_when_both_clocks_were_recorded",
     "doc": ""
    },
    {
     "name": "test_alignment_is_bounded_when_only_the_generator_clock_was_recorded",
     "doc": "A schema 2.0 run gets an interval, not an estimate."
    },
    {
     "name": "test_the_fault_position_is_an_interval_under_a_bounded_alignment",
     "doc": "The figure caveat is data, not prose: a band cannot be drawn as a line."
    },
    {
     "name": "test_a_measured_alignment_puts_the_fault_on_the_throughput_axis",
     "doc": ""
    },
    {
     "name": "test_an_availability_rto_below_the_audit_cadence_is_not_quotable",
     "doc": "0.07 s against a 0.40 s sampling interval is not a recovery time."
    },
    {
     "name": "test_an_availability_rto_above_the_cadence_is_quotable",
     "doc": ""
    },
    {
     "name": "test_availability_rto_is_rederived_from_the_audit_log_when_present",
     "doc": "The figure is recomputed from its observations, not taken on trust."
    },
    {
     "name": "test_a_new_stable_state_is_reported_as_undefined_not_as_no_recovery",
     "doc": "A new stable state below the threshold is reported as undefined, not as no recovery."
    },
    {
     "name": "test_a_recovering_run_reports_a_performance_rto",
     "doc": ""
    },
    {
     "name": "test_a_bounded_alignment_makes_the_performance_rto_an_interval",
     "doc": "The unmeasured clock offset propagates into the figure, visibly."
    },
    {
     "name": "test_the_metrics_schema_is_pinned",
     "doc": "Changing the measurement schema must be a deliberate, reviewed edit."
    },
    {
     "name": "test_every_phase_writes_a_row_matching_the_declared_schema",
     "doc": "Catch schema drift statically, not thirty minutes into a measurement."
    },
    {
     "name": "test_a_run_whose_preflight_failed_is_refused",
     "doc": "Pre-flight is a separate gate from validation and must be enforced too."
    },
    {
     "name": "test_the_overlap_remedy_does_not_advise_raising_a_saturated_phase",
     "doc": "Once the slower engine has saturated, more concurrency cannot close the gap."
    },
    {
     "name": "test_the_overlap_remedy_suggests_extending_a_still_rising_phase",
     "doc": "While the slower phase is still climbing, extending its sweep is right."
    },
    {
     "name": "test_interpolation_does_not_cross_the_saturation_fold",
     "doc": "Past saturation one throughput maps to two latencies; don't average them."
    },
    {
     "name": "test_matched_throughput_reports_overhead_where_the_ranges_meet",
     "doc": "Matched throughput reports latency overhead where the two ranges meet."
    },
    {
     "name": "test_matched_throughput_reports_how_loaded_each_phase_is",
     "doc": "Matched throughput reports each engine's utilisation, since the gap matters."
    },
    {
     "name": "test_a_comparison_across_different_hardware_is_refused",
     "doc": "Identical flags on unlike machines are not an identical configuration."
    },
    {
     "name": "test_an_unrecorded_machine_warns_rather_than_passing_silently",
     "doc": ""
    },
    {
     "name": "test_provider_memory_rounding_does_not_fire_the_hardware_check",
     "doc": "The two real machines report 4,005,712 and 4,007,012 kB. Erroring on a\n0.03% difference would make the check fire on every legitimate Phase II/III\ncomparison, and a check that rejects correct data gets disabled."
    },
    {
     "name": "test_a_materially_different_memory_size_still_fires",
     "doc": ""
    },
    {
     "name": "test_matched_utilisation_compares_at_different_throughputs_by_design",
     "doc": ""
    },
    {
     "name": "test_matched_throughput_can_never_reach_equal_utilisation",
     "doc": "The gap is T * (1/peak_iii - 1/peak_ii): linear in T, zero only at zero\nload. This is why both comparisons exist rather than one superseding the\nother -- no amount of extra tiers closes it."
    },
    {
     "name": "test_a_single_worker_tier_is_unqueued_structurally_not_statistically",
     "doc": "One worker means one operation outstanding, so there is nothing to wait\nbehind. Gating this on a Little's-law threshold denied a structurally\nimpossible queue on a 5.1% blend artefact."
    },
    {
     "name": "test_a_phase_that_never_reached_one_worker_is_not_called_unqueued",
     "doc": ""
    },
    {
     "name": "test_a_hardware_difference_can_be_accepted_explicitly_and_is_recorded",
     "doc": "A hardware difference can be accepted explicitly, and is then recorded as a warning."
    },
    {
     "name": "test_accepting_hardware_does_not_excuse_a_workload_difference",
     "doc": "The override is scoped to hardware. A seed mismatch still refuses."
    },
    {
     "name": "test_the_unqueued_ratio_is_computed_before_rounding",
     "doc": "Rounding an input, dividing, then rounding again carries the first\nrounding's error into the result. It produced two figures for one quantity\n(50.37x against 50.38x), which a dissertation then has to reconcile."
    },
    {
     "name": "test_the_same_concurrency_throughput_ratio_is_computed_before_rounding",
     "doc": "The same-concurrency throughput ratio is computed from unrounded values."
    },
    {
     "name": "test_a_matched_utilisation_level_is_not_rounded_before_it_is_used",
     "doc": "Rounding a level and multiplying it back by the peak moves the point."
    },
    {
     "name": "test_write_latency_that_returns_to_baseline_is_reported_as_such",
     "doc": ""
    },
    {
     "name": "test_a_permanent_write_latency_shift_is_reported_even_though_throughput_recovers",
     "doc": "The exact scenario a TPS-only view misses: quorum now needs a slower\nmember, so the write path is permanently ~2x, but reads dominate throughput\nand it looks fully recovered on that axis alone."
    },
    {
     "name": "test_write_latency_still_changing_is_reported_as_unsettled",
     "doc": ""
    },
    {
     "name": "test_write_latency_recovery_reports_unavailable_without_a_fault",
     "doc": ""
    },
    {
     "name": "test_a_fault_on_a_follower_removes_it_from_the_leaders_row",
     "doc": "azure-1 dying while gcp-1 stays leaseholder: remove one entry from the\ngateway's own row. This is the case the function was first written for."
    },
    {
     "name": "test_a_fault_on_the_leaseholder_is_reported_as_a_range_not_a_false_point",
     "doc": "With the leaseholder as target, every survivor is a candidate leader and a range is\nreported."
    },
    {
     "name": "test_the_consequence_text_is_engine_aware_about_the_read_path",
     "doc": "Reads are unaffected on CockroachDB but follow the primary on PostgreSQL."
    },
    {
     "name": "test_the_displaced_case_evaluates_every_survivor_as_a_candidate",
     "doc": ""
    }
   ]
  },
  {
   "path": "tests/test_chaos.py",
   "doc": "Tests for Phases III-IV recovery detection.",
   "tests": [
    {
     "name": "test_rto_is_the_start_of_the_sustained_window_not_its_end",
     "doc": "The hold qualifies the recovery; it must not postpone the timestamp."
    },
    {
     "name": "test_a_transient_spike_does_not_count_as_recovery",
     "doc": "One good sample inside a degraded period must not end the outage."
    },
    {
     "name": "test_no_recovery_is_reported_when_throughput_never_returns",
     "doc": ""
    },
    {
     "name": "test_recovery_is_not_declared_without_enough_samples_to_establish_the_hold",
     "doc": "A run that ends mid-window must report no recovery rather than guess."
    },
    {
     "name": "test_recovery_exactly_at_the_threshold_qualifies",
     "doc": "The floor is inclusive: 'at or above' the threshold."
    },
    {
     "name": "test_degradation_below_the_floor_by_a_hair_does_not_qualify",
     "doc": ""
    },
    {
     "name": "test_samples_before_the_fault_are_ignored",
     "doc": "Pre-fault throughput trivially exceeds the floor and must not be matched."
    },
    {
     "name": "test_availability_rto_is_the_first_acknowledged_write_after_the_fault",
     "doc": "The question is when the database accepted a write again, not when\nthroughput recovered. Fault at t=10; writes fail until 12.5."
    },
    {
     "name": "test_availability_rto_is_zero_when_writes_never_stopped",
     "doc": "A fault on a node that is not in the write path interrupts nothing."
    },
    {
     "name": "test_availability_rto_is_none_when_writes_never_resume",
     "doc": ""
    },
    {
     "name": "test_an_audit_writer_that_stopped_observing_reports_no_rto_at_all",
     "doc": "An audit writer that stopped observing reports no RTO, not a tiny one."
    },
    {
     "name": "test_a_writer_still_blocked_at_the_end_of_the_run_is_not_counted_as_covering_it",
     "doc": "Coverage is judged on acknowledgements, not on attempts."
    },
    {
     "name": "test_full_coverage_still_reports_no_interruption_as_a_result",
     "doc": "The control. Coverage reaching the end of the run is the ordinary case,\nand an instrument that watched throughout and saw nothing has measured\nsomething -- that must not become an \"unmeasured\" verdict."
    },
    {
     "name": "test_coverage_is_not_judged_at_all_when_the_run_end_is_unknown",
     "doc": "Runs recorded before the field existed must read exactly as they did."
    },
    {
     "name": "test_resolution_is_reported_so_the_figure_can_be_qualified",
     "doc": "An RTO below the audit cadence is indistinguishable from no outage."
    },
    {
     "name": "test_the_single_node_answering_200_is_the_primary",
     "doc": ""
    },
    {
     "name": "test_no_primary_found_refuses_rather_than_guessing",
     "doc": ""
    },
    {
     "name": "test_two_primaries_is_a_split_brain_and_refuses_rather_than_picking_one",
     "doc": ""
    },
    {
     "name": "test_every_fault_payload_is_privileged",
     "doc": "A payload without sudo is silently refused on any non-root SSH user."
    },
    {
     "name": "test_dead_payload_kills_the_right_process_per_engine",
     "doc": ""
    },
    {
     "name": "test_the_postgresql_dead_fault_disables_restart_with_a_drop_in_not_set_property",
     "doc": "The PostgreSQL dead fault disables restarts with a drop-in (set-property cannot set\nRestart=)."
    },
    {
     "name": "test_the_postgresql_dead_fault_cannot_be_undone_by_systemd",
     "doc": "Patroni's Restart=on-failure would undo the kill in ~100 ms, so restarts are\ndisabled first."
    },
    {
     "name": "test_the_postgresql_dead_fault_signals_the_unit_not_a_process_name",
     "doc": "The kill signals the unit's cgroup; Patroni's process name is python3, not patroni."
    },
    {
     "name": "test_restoring_postgresql_removes_the_restart_override",
     "doc": "Otherwise the node comes back with Restart=no still in place and the next\ndead run measures a node systemd has stopped supervising."
    },
    {
     "name": "test_the_authorisation_probe_is_privileged_and_harmless",
     "doc": "It must need the same rights as the fault while changing nothing."
    },
    {
     "name": "test_a_refused_injection_is_recorded_as_not_landed",
     "doc": ""
    },
    {
     "name": "test_a_successful_injection_is_recorded_as_landed",
     "doc": ""
    },
    {
     "name": "test_a_transport_error_is_not_treated_as_a_refusal",
     "doc": "For a `dead` fault, losing the connection is evidence the fault landed."
    },
    {
     "name": "test_preflight_fails_when_the_fault_would_not_be_permitted",
     "doc": ""
    },
    {
     "name": "test_preflight_passes_when_the_fault_would_be_permitted",
     "doc": ""
    },
    {
     "name": "test_a_profile_that_already_observes_long_enough_is_left_alone",
     "doc": ""
    },
    {
     "name": "test_a_run_too_short_to_observe_the_recovery_is_extended",
     "doc": "inject_at_s is measured from the first sample, so duration_s alone\ncannot guarantee anything about what follows the fault."
    },
    {
     "name": "test_the_window_is_never_shortened",
     "doc": ""
    },
    {
     "name": "test_recover_mode_observes_from_the_heal_not_from_the_fault",
     "doc": "In recover mode the post-fault window is counted from the heal, not from the fault."
    },
    {
     "name": "test_the_smoke_profile_can_actually_observe_a_recover_recovery",
     "doc": "The smoke profile runs long enough to observe a recover-mode recovery."
    },
    {
     "name": "test_thesis_extended_recover_run_is_long_enough_to_settle",
     "doc": "thesis-extended runs long enough after the fault for the settled-state test to resolve."
    },
    {
     "name": "test_thesis_extended_dead_run_is_long_enough_to_settle",
     "doc": ""
    },
    {
     "name": "test_thesis_recover_run_is_long_enough_to_settle",
     "doc": "thesis runs long enough after the fault for the settled-state test to resolve."
    },
    {
     "name": "test_thesis_dead_run_is_long_enough_to_settle",
     "doc": ""
    },
    {
     "name": "test_thesis_extended_still_has_the_longer_window",
     "doc": "thesis.yaml (450s) and thesis-extended.yaml (900s) were raised by\ndifferent amounts deliberately -- this pins that they didn't drift back\ninto agreement."
    },
    {
     "name": "test_the_payload_and_the_run_length_read_the_same_heal_delay",
     "doc": "Two numbers that must never drift apart: if the payload's sleep and the\nrun's length disagree, the run is silently either too short to observe the\nrecovery or padded with dead time."
    },
    {
     "name": "test_the_restart_is_privileged",
     "doc": "/var/lib/cockroach is root-owned and the SSH user is not root."
    },
    {
     "name": "test_the_restored_node_does_not_try_to_rejoin_via_itself",
     "doc": ""
    },
    {
     "name": "test_liveness_is_read_from_a_survivor_not_from_the_fault_target",
     "doc": "Polling the killed node always answers 0 live, which is what made\nrun-experiment.sh warn on every successful dead-mode run."
    },
    {
     "name": "test_a_node_that_never_comes_back_is_reported_as_not_rejoined",
     "doc": ""
    },
    {
     "name": "test_postgresql_restarts_patroni_rather_than_cockroach",
     "doc": ""
    },
    {
     "name": "test_primary_already_on_the_gateway_passes_without_switching_over",
     "doc": ""
    },
    {
     "name": "test_primary_elsewhere_is_switched_over_to_the_gateway",
     "doc": ""
    },
    {
     "name": "test_a_candidate_that_is_still_catching_up_is_waited_for_not_refused",
     "doc": "Phase IV must survive following Phase III on the PostgreSQL arm."
    },
    {
     "name": "test_a_replica_that_is_up_but_not_streaming_is_not_a_candidate",
     "doc": "A replica answering /replica 200 but not streaming is not a switchover candidate."
    },
    {
     "name": "test_a_streaming_replica_left_on_an_older_timeline_is_not_a_candidate",
     "doc": "A streaming replica on an older timeline than the leader is not a candidate."
    },
    {
     "name": "test_a_streaming_correctly_timelined_replica_can_still_not_be_a_quorum_member",
     "doc": "A streaming replica on the right timeline is not a candidate until it is a quorum member."
    },
    {
     "name": "test_the_wait_holds_for_quorum_membership_after_streaming_is_already_true",
     "doc": "The candidate can flip to streaming-on-timeline well before Patroni\nadmits it to the synchronous set; the wait must span both transitions, not\njust the first."
    },
    {
     "name": "test_an_ineligible_candidate_still_fails_once_the_window_expires",
     "doc": "The window is a wait, not a loosening: the condition is unchanged, and\nthe reason Patroni last gave is reported rather than a bare timeout."
    },
    {
     "name": "test_bench_and_net_probe_still_fail_fast_with_no_settle_window",
     "doc": "Default 0 means one reading, exactly as before. Only the chaos phases\npass a window, mirroring check_leaseholder_placement."
    },
    {
     "name": "test_a_switchover_that_does_not_take_fails_rather_than_reporting_success",
     "doc": ""
    },
    {
     "name": "test_no_primary_at_all_fails_the_check_rather_than_raising",
     "doc": ""
    }
   ]
  },
  {
   "path": "tests/test_figures.py",
   "doc": "Tests for figure filenames carrying their own provenance.",
   "tests": [
    {
     "name": "test_the_slug_names_engine_profile_and_run",
     "doc": ""
    },
    {
     "name": "test_the_engine_is_named_even_when_it_is_the_default",
     "doc": "Unlike the suffix this replaced, cockroachdb is not the blank case any\nmore. A filename that names one engine only when it is the unusual one\nstill leaves the reader inferring the other from its absence."
    },
    {
     "name": "test_the_same_engine_at_two_profiles_does_not_collide",
     "doc": "The concrete case: a smoke self-test and a thesis-scale sweep both\nrendered into figures/. Before the profile was in the name, the second\nsilently replaced the first."
    },
    {
     "name": "test_two_runs_of_the_same_profile_and_engine_do_not_collide",
     "doc": "Re-running the same profile is the common case, and the run id is the\nonly thing that separates the two renders."
    },
    {
     "name": "test_runs_that_disagree_get_mixed_rather_than_a_guess",
     "doc": "A figure overlaying both engines' runs is a real, if currently unused,\ncall shape (`throughput_sweep` takes a sequence). Tagging it with one\nengine's name would misattribute it; every run id is still listed."
    },
    {
     "name": "test_phase_one_is_named_like_every_other_figure",
     "doc": "Phase I figures carry engine and profile too: each deployment is a different set of\nmachines."
    },
    {
     "name": "test_a_run_with_no_recorded_engine_defaults_to_cockroachdb",
     "doc": "Every run written before Manifest.engine existed was a CockroachDB run --\nthe flag that lets --engine postgresql be requested didn't exist either."
    },
    {
     "name": "test_none_entries_are_ignored_not_treated_as_a_third_run",
     "doc": ""
    },
    {
     "name": "test_slug_components_are_filename_safe",
     "doc": "Profile names and run ids reach the filesystem verbatim otherwise."
    },
    {
     "name": "test_resilience_filenames_stay_distinct_for_both_fault_classes",
     "doc": ""
    },
    {
     "name": "test_an_unnamed_fault_class_still_gets_its_own_name_and_provenance",
     "doc": ""
    },
    {
     "name": "test_the_named_figure_pairs_never_collide_across_engines",
     "doc": "Run the thesis sweep against both engines and every figure from one must\nsurvive the other."
    },
    {
     "name": "test_every_figure_is_reported_as_both_a_png_and_a_vector_file",
     "doc": "`report figures` prints what it wrote, and the vector file is written by\nthe same call -- listing only the PNG hid half the output."
    },
    {
     "name": "test_no_pdf_is_written_any_more",
     "doc": ""
    }
   ]
  },
  {
   "path": "tests/test_hardware_metrics.py",
   "doc": "Tests for the node_exporter poller in crdblab/core/hardware_metrics.py.",
   "tests": [
    {
     "name": "test_parses_the_metric_families_this_project_reads_and_ignores_the_rest",
     "doc": ""
    },
    {
     "name": "test_the_first_sample_per_node_has_no_rate_yet",
     "doc": "No prior scrape to difference against: rates are '', not 0 or NaN."
    },
    {
     "name": "test_cpu_busy_pct_is_one_minus_idle_over_total_across_all_cores",
     "doc": ""
    },
    {
     "name": "test_disk_and_network_rates_are_deltas_over_elapsed_time",
     "doc": ""
    },
    {
     "name": "test_network_rate_excludes_loopback",
     "doc": "`lo`'s counters jump by ~900000 between scrapes; a correct rate (from\neth0 alone) is 60.0 and 12.0 bytes/s. Anything near 180000/12.0 means `lo`\nleaked into the sum."
    },
    {
     "name": "test_a_node_that_fails_to_scrape_does_not_block_the_others",
     "doc": ""
    },
    {
     "name": "test_hardware_metrics_columns_round_trip_through_metricswriter",
     "doc": "Guards against the sampler's row-builder drifting out of sync with the\ndeclared schema -- MetricsWriter raises ValueError on any mismatch."
    }
   ]
  },
  {
   "path": "tests/test_insights.py",
   "doc": "Tests for the insights catalogue.",
   "tests": [
    {
     "name": "test_a_stat_is_printed_to_four_significant_figures",
     "doc": ""
    },
    {
     "name": "test_nested_stats_print_as_pairs_and_series_as_a_length",
     "doc": ""
    },
    {
     "name": "test_summary_csv_flattens_with_dots_and_indices",
     "doc": ""
    },
    {
     "name": "test_the_catalogue_is_thirty_one_charts_in_five_groups",
     "doc": ""
    },
    {
     "name": "test_a_chart_filename_stem_is_its_id_and_title",
     "doc": ""
    },
    {
     "name": "test_an_empty_runs_directory_skips_every_chart_with_a_reason",
     "doc": ""
    },
    {
     "name": "test_a_run_that_fails_the_gate_is_refused_not_charted",
     "doc": ""
    },
    {
     "name": "test_every_chart_draws_from_the_smoke_runs",
     "doc": ""
    },
    {
     "name": "test_filenames_carry_engine_profile_and_runs",
     "doc": ""
    },
    {
     "name": "test_numbers_match_the_recorded_smoke_render",
     "doc": "Values the 2026-09-23 render of these same runs recorded."
    }
   ]
  },
  {
   "path": "tests/test_network_probe.py",
   "doc": "Tests for Phase I substrate characterisation.",
   "tests": [
    {
     "name": "test_summary_line_supplies_precision_the_packet_lines_lack",
     "doc": "Central statistics must not inherit the per-packet print resolution."
    },
    {
     "name": "test_per_packet_resolution_is_recorded_not_implied",
     "doc": "Quantiles come from the packet lines, so their precision is link-dependent."
    },
    {
     "name": "test_quantiles_are_not_rounded_beyond_their_resolution",
     "doc": ""
    },
    {
     "name": "test_unreachable_destination_is_recorded_not_dropped",
     "doc": "A dead link must appear in the matrix, not vanish from it."
    },
    {
     "name": "test_missing_summary_line_falls_back_rather_than_failing",
     "doc": ""
    },
    {
     "name": "test_nearest_rank_quantile_is_exact_at_the_boundaries",
     "doc": "Nearest-rank indexing is exact at the boundaries, including for small samples."
    },
    {
     "name": "test_quorum_floor_is_the_ack_that_completes_the_majority",
     "doc": "Five voters need the leader plus two followers, so the second-fastest binds."
    },
    {
     "name": "test_quorum_floor_rejects_a_configuration_that_cannot_form_a_majority",
     "doc": ""
    }
   ]
  },
  {
   "path": "tests/test_pipeline.py",
   "doc": "Pipeline helpers: which tailnet devices get deleted, and the per-engine DB_URI.",
   "tests": [
    {
     "name": "test_six_vms_including_the_client",
     "doc": ""
    },
    {
     "name": "test_exact_and_collision_renamed_devices_match",
     "doc": ""
    },
    {
     "name": "test_similar_but_different_names_do_not_match",
     "doc": ""
    },
    {
     "name": "test_workstation_is_never_matched",
     "doc": ""
    },
    {
     "name": "test_db_uri_cockroachdb_lists_every_member_gateway_first",
     "doc": ""
    },
    {
     "name": "test_db_uri_postgresql_goes_through_haproxy_with_password",
     "doc": ""
    },
    {
     "name": "test_db_uri_rejects_unknown_engine",
     "doc": ""
    }
   ]
  },
  {
   "path": "tests/test_preflight.py",
   "doc": "Tests for the pre-flight assertions.",
   "tests": [
    {
     "name": "test_a_clean_window_reports_the_differenced_rate",
     "doc": ""
    },
    {
     "name": "test_a_seed_mismatch_still_fails_on_a_clean_window",
     "doc": "The seed-mismatch signature: statements execute, no rows are touched."
    },
    {
     "name": "test_a_mid_tier_statistics_flush_does_not_read_as_no_work",
     "doc": "CockroachDB flushes in-memory statement statistics every 10 minutes."
    },
    {
     "name": "test_the_flush_fallback_still_catches_a_seed_mismatch",
     "doc": "The narrowed window must not become an escape hatch for a no-rows workload."
    },
    {
     "name": "test_a_vanished_counter_fails_whatever_it_is_called",
     "doc": "Counters that stood at 1000 and now read 0 fail, uncorroborated."
    },
    {
     "name": "test_the_hardware_block_is_parsed_into_its_three_fields",
     "doc": ""
    },
    {
     "name": "test_a_missing_field_is_recorded_as_unknown_never_as_a_default",
     "doc": "A defaulted CPU count would compare equal to a real reading and so make\ntwo unlike machines look alike."
    },
    {
     "name": "test_the_note_round_trips_through_the_manifest",
     "doc": ""
    },
    {
     "name": "test_a_post_tier_flush_is_not_reported_as_the_workload_never_running",
     "doc": "The old message said \"the workload may not have run at all\" about a tier\nthat had just sustained 611.7 ops/s for 55 intervals. That is a wrong fact,\nnot merely an unhelpful one."
    },
    {
     "name": "test_a_post_tier_flush_is_survivable_when_the_quorum_floor_corroborates",
     "doc": ""
    },
    {
     "name": "test_corroboration_never_rescues_a_measured_seed_mismatch",
     "doc": "Corroboration applies only where there is no evidence. A window that did\nproduce a measurement is asserted on, whatever the floor check said -- an\nescape hatch here would disable the only no-rows detector."
    },
    {
     "name": "test_a_run_with_no_statements_at_all_still_fails_outright",
     "doc": "c0 == 0 and c1 == 0 is genuinely no work, not a flush."
    },
    {
     "name": "test_an_unmeasured_rate_is_null_in_the_manifest_not_zero_or_nan",
     "doc": ""
    },
    {
     "name": "test_correct_placement_passes_without_waiting",
     "doc": ""
    },
    {
     "name": "test_the_default_is_a_single_reading_so_existing_callers_are_unchanged",
     "doc": "bench and net probe must still fail fast; only chaos waits."
    },
    {
     "name": "test_placement_that_converges_within_the_window_passes",
     "doc": "The assertion still has to hold -- it is just given time to become true."
    },
    {
     "name": "test_placement_that_never_converges_still_fails",
     "doc": "Waiting is not the same as accepting; a cluster that does not recover\nits declared placement must not be measured."
    },
    {
     "name": "test_the_postgresql_server_probe_cannot_match_its_own_command_line",
     "doc": "The PostgreSQL server probe's pgrep patterns cannot match the probe's own command line."
    },
    {
     "name": "test_hardware_is_captured_for_both_engines",
     "doc": "The half of the capture that must not differ between the engines. The\nserver's flags and version are expected to differ -- that is the comparison\n-- but a cross-engine result drawn across unlike machines is still confounded."
    },
    {
     "name": "test_pg_row_match_passes_when_every_scan_fetches_a_row",
     "doc": ""
    },
    {
     "name": "test_pg_row_match_catches_a_workload_touching_nothing",
     "doc": "The seed-mismatch signature on PostgreSQL: the scans happen, no rows come back, and\nthroughput goes *up* because an operation that matches nothing does no\nwork."
    },
    {
     "name": "test_pg_row_match_reports_a_workload_that_never_ran",
     "doc": ""
    },
    {
     "name": "test_pg_row_match_treats_a_counter_reset_as_evidence_lost_not_as_a_pass",
     "doc": "pg_stat counters going backwards means the server restarted mid-tier.\nWithout an independent detector there is nothing left to assert on."
    },
    {
     "name": "test_row_match_probe_factory_picks_the_detector_per_engine",
     "doc": ""
    },
    {
     "name": "test_pg_row_match_ignores_rows_merely_read_by_a_sequential_scan",
     "doc": "seq_tup_read counts rows read, not matched, so it must not inflate the match rate."
    },
    {
     "name": "test_the_pg_stats_query_reads_no_sequential_row_counter",
     "doc": ""
    },
    {
     "name": "test_pg_memory_note_round_trips_through_the_manifest",
     "doc": ""
    },
    {
     "name": "test_capture_pg_memory_config_parses_a_successful_probe",
     "doc": ""
    },
    {
     "name": "test_capture_pg_memory_config_returns_none_on_probe_failure",
     "doc": ""
    },
    {
     "name": "test_capture_server_config_cockroachdb_makes_no_extra_ssh_call",
     "doc": "CockroachDB has no PostgreSQL-only budget to probe -- confirming this\nstays a single round trip, not two, for the engine that gains nothing\nfrom the second one."
    }
   ]
  },
  {
   "path": "tests/test_rto_probe.py",
   "doc": "Tests for the high-frequency RTO probe.",
   "tests": [
    {
     "name": "test_the_probe_writes_continuously_on_a_background_thread",
     "doc": "The workload's path is a thread that does not exist here."
    },
    {
     "name": "test_concurrency_is_what_makes_the_sampling_finer_than_the_write_cost",
     "doc": "A serial client cannot observe a 10 ms write more often than every 10 ms."
    },
    {
     "name": "test_the_achieved_cadence_is_reported_and_is_not_the_configured_one",
     "doc": "Quoting the dispatch interval as the resolution would be false precision."
    },
    {
     "name": "test_a_failure_and_the_reconnect_after_it_reach_the_log_file",
     "doc": "The separate log is the crash-proof record of the outage edges."
    },
    {
     "name": "test_the_probe_never_raises_into_the_run_it_is_observing",
     "doc": "A probe that can fail a chaos run is a new way to lose an hour of testbed."
    },
    {
     "name": "test_offsets_are_taken_from_the_epoch_the_caller_supplies",
     "doc": "Offsets use the epoch the caller supplies, so every file in a run shares one clock."
    },
    {
     "name": "test_a_timeout_is_not_the_same_observation_as_a_refusal",
     "doc": "The three failure kinds mean different things for a downtime figure."
    },
    {
     "name": "test_rto_runs_from_the_fault_to_the_end_of_the_outage_that_followed_it",
     "doc": "Not to the next write served, which on this cluster usually succeeds."
    },
    {
     "name": "test_detection_lag_is_reported_separately_from_recovery",
     "doc": "Writes keep committing until the cluster notices a node is gone."
    },
    {
     "name": "test_an_interval_shorter_than_the_sampling_gap_is_not_quoted_as_a_number",
     "doc": "Below its own resolution the probe has not measured an outage."
    },
    {
     "name": "test_a_probe_that_stopped_observing_does_not_claim_nothing_was_detectable",
     "doc": "A probe that stopped observing reports the outage as unmeasured, not undetectable."
    },
    {
     "name": "test_a_probe_that_watched_to_the_end_still_reports_nothing_detectable",
     "doc": "The control: full coverage and a quiet cluster is a measurement."
    },
    {
     "name": "test_coverage_is_not_judged_when_the_run_end_is_unknown",
     "doc": "Runs recorded before the field existed must read exactly as before."
    },
    {
     "name": "test_a_run_that_ended_during_the_outage_reports_no_rto_rather_than_a_bound",
     "doc": "The probe cannot see a recovery that happened after it stopped."
    },
    {
     "name": "test_an_in_flight_write_dates_the_recovery_more_tightly_than_a_later_one",
     "doc": "A blocked INSERT returns the moment the range is served again."
    },
    {
     "name": "test_outage_windows_are_ordered_by_duration_and_carry_both_edges",
     "doc": ""
    },
    {
     "name": "test_summarise_reports_no_resolution_rather_than_zero_when_nothing_was_served",
     "doc": "An unmeasured quantity must not be indistinguishable from one measured as\nzero. A resolution of 0.0 s would read as perfect precision."
    },
    {
     "name": "test_the_csv_round_trips_into_the_same_rto",
     "doc": "The analysis layer re-derives from disk; it does not trust the summary."
    },
    {
     "name": "test_rows_match_the_declared_schema_exactly",
     "doc": "MetricsWriter rejects a row that does not, so this fails at the writer\nrather than producing a file the analysis layer silently misreads."
    },
    {
     "name": "test_validation_accepts_a_sound_probe_log",
     "doc": ""
    },
    {
     "name": "test_validation_rejects_a_write_that_returned_before_it_was_sent",
     "doc": "Two offsets on different clocks would move an outage edge without making\nany single value look wrong."
    },
    {
     "name": "test_validation_rejects_a_repeated_sequence_number",
     "doc": ""
    },
    {
     "name": "test_validation_flags_a_refused_write_as_a_probe_defect_not_an_outage",
     "doc": ""
    },
    {
     "name": "test_validation_rejects_a_log_in_which_nothing_was_ever_served",
     "doc": "With no served write there is no baseline, so there is no outage to\nmeasure -- the probe never reached the database."
    },
    {
     "name": "test_validation_rejects_an_outcome_nobody_declared",
     "doc": ""
    },
    {
     "name": "test_resilience_rederives_the_probe_rto_from_the_run_directory",
     "doc": "``analyze resilience`` reads the observations, not the phase's summary."
    },
    {
     "name": "test_a_run_without_a_probe_log_says_so_rather_than_reporting_nothing",
     "doc": "Every committed run predates the probe. They must keep analysing, and the\nabsence must be legible rather than an empty section."
    },
    {
     "name": "test_a_disabled_probe_is_distinguished_from_a_missing_one",
     "doc": "\"Turned off\" and \"did not exist yet\" are different facts about a run, and\nonly one of them is a reason to re-measure."
    },
    {
     "name": "test_the_chaos_summary_prints_the_probe_beside_the_audit_figure",
     "doc": "Two measurements of the same quantity are only useful when compared."
    },
    {
     "name": "test_a_probe_that_failed_is_reported_as_a_failed_probe",
     "doc": "And not as an outage, and not silently. A broken probe is a fact about the\ninstrument; letting it print an empty section would invite the reader to\nassume there was nothing to see."
    },
    {
     "name": "test_a_run_whose_probe_log_is_corrupt_will_not_load",
     "doc": "The loader is the only way into a run, and it gates on the probe too."
    },
    {
     "name": "test_the_standalone_probe_command_produces_a_normal_run_directory",
     "doc": "`crdblab probe rto` is a measurement, so it leaves a manifest like any other."
    },
    {
     "name": "test_the_pool_does_not_phase_lock_into_bursts",
     "doc": "Eight workers must give eight spread observations, not eight at once."
    },
    {
     "name": "test_resolution_is_the_tail_of_the_gap_distribution_not_the_median",
     "doc": "A bimodal sampling pattern must not be reported by its flattering mode."
    },
    {
     "name": "test_the_outage_threshold_is_not_raised_by_the_outage_itself",
     "doc": "Sampling resolution is characterised from the healthy period only."
    },
    {
     "name": "test_a_longer_post_fault_window_does_not_manufacture_an_outage",
     "doc": "A longer post-fault window alone does not produce an attributed outage."
    },
    {
     "name": "test_a_genuine_rate_increase_after_the_fault_is_attributable",
     "doc": "The other side of the same test: a real change in the exceedance rate\nmust still be reported as a recovery time, or the fix would have traded a\nfalse positive for blindness to true positives."
    },
    {
     "name": "test_attribution_declines_to_call_it_with_too_few_pre_fault_observations",
     "doc": "A fault seconds into the run cannot be tested against a tail nobody\ncharacterised yet. The result must say so rather than default either way."
    },
    {
     "name": "test_in_flight_fraction_is_clamped_to_one_when_the_write_started_early",
     "doc": "A fraction greater than one is not a fraction of anything."
    },
    {
     "name": "test_in_flight_fraction_never_exceeds_one",
     "doc": "Property check across many overlap shapes, not just the one that was\ncaught: whatever the relative timing of dispatch and completion, the\nreported fraction must stay in [0, 1] because it is documented as one."
    },
    {
     "name": "test_agent_offsets_are_rebased_onto_the_runs_clock",
     "doc": "An agent offset is placed by the gap between the two epochs."
    },
    {
     "name": "test_attempts_arriving_before_the_epoch_are_dropped_not_guessed",
     "doc": "Without the epoch line there is no origin, and inventing one is the bug."
    },
    {
     "name": "test_an_unparseable_agent_epoch_is_an_error_not_a_zero_skew",
     "doc": ""
    },
    {
     "name": "test_malformed_attempt_lines_do_not_abort_the_run",
     "doc": "A probe that could kill the measurement it observes is a new failure mode."
    },
    {
     "name": "test_summary_records_where_the_probe_ran_and_the_skew_it_applied",
     "doc": "Both are part of the measurement and must reach the run directory."
    },
    {
     "name": "test_the_agent_ships_only_stdlib_only_modules",
     "doc": "The agent's dependency surface is psycopg plus the standard library."
    },
    {
     "name": "test_the_outage_is_the_largest_post_fault_gap_not_the_first_over_the_floor",
     "doc": "A short blip right after the fault must not mask the real interruption."
    }
   ]
  },
  {
   "path": "tests/test_topology.py",
   "doc": "Tests for the declared testbed topology.",
   "tests": [
    {
     "name": "test_the_gateway_is_the_gcp_node",
     "doc": "The gateway is the GCP node; the client node is separate."
    },
    {
     "name": "test_exactly_one_node_is_the_gateway",
     "doc": "``Topology.gateway`` raises rather than picking one, and every phase calls\nit. A second gateway flag would fail the whole harness at the first phase,\nwhich is the intended behaviour, but it is cheaper to fail here."
    },
    {
     "name": "test_the_gateway_is_inside_the_lease_preference_triangle",
     "doc": "Leaseholders are pinned to us-east, us-east1 and us-west."
    },
    {
     "name": "test_the_client_node_is_not_a_member_of_the_cluster",
     "doc": "``len(topology)`` is used as the voter count, so admitting the\nclient node into it would corrupt the quorum arithmetic."
    },
    {
     "name": "test_the_chaos_target_default_is_not_the_gateway",
     "doc": "Failing the node the generator and both audit clients run on would remove\nthe measurement apparatus along with the node under test. ``p4_chaos.run``\nrefuses that at run time; this catches it in a profile review instead."
    },
    {
     "name": "test_cluster_target_generates_a_single_gateway_uri_for_cockroachdb",
     "doc": "Not one URI per cluster member."
    },
    {
     "name": "test_cluster_target_generates_a_single_local_uri_for_postgresql",
     "doc": "One host (the client node's pgbouncer, which forwards to the HAProxy that\nfollows Patroni's leader) and a password: Patroni's pg_hba is md5 for every host connection, so an\nuncredentialed DSN is refused before the generator sends an operation."
    },
    {
     "name": "test_cockroachdb_target_stays_uncredentialed",
     "doc": "CockroachDB runs --insecure here and takes root with no password; adding\none to that DSN would change what the CockroachDB half of the comparison\nconnects as."
    },
    {
     "name": "test_a_password_with_url_metacharacters_is_escaped",
     "doc": "A password is copied into a URL, so `@` or `/` in one would otherwise\nre-parse the DSN into a different host."
    },
    {
     "name": "test_the_generator_gets_one_host_and_the_measurement_clients_get_all_five",
     "doc": "The generator gets one URL (HAProxy); the audit writer and probe get all five hosts."
    },
    {
     "name": "test_the_measurement_clients_bound_established_connections_not_just_new_ones",
     "doc": "A black-holed socket must fail, not block forever."
    },
    {
     "name": "test_dsn_passwords_are_escaped_in_both_builders",
     "doc": ""
    }
   ]
  },
  {
   "path": "tests/test_validation.py",
   "doc": "Tests for the post-run consistency checks.",
   "tests": [
    {
     "name": "test_littles_law_accepts_a_heterogeneous_workload",
     "doc": "The bound is the frequency-weighted median, not the slowest component."
    },
    {
     "name": "test_littles_law_still_catches_latency_bound_to_the_wrong_column",
     "doc": "Binding p95 into the p50 column inflates the weighted median."
    },
    {
     "name": "test_littles_law_catches_throughput_over_counted",
     "doc": "A cumulative total admitted as an interval sample collapses N/X."
    },
    {
     "name": "test_littles_law_is_insensitive_to_under_counted_throughput",
     "doc": "Documents the direction this check does *not* cover."
    },
    {
     "name": "test_littles_law_ignores_ticks_with_no_throughput",
     "doc": ""
    },
    {
     "name": "test_plausibility_catches_a_cumulative_total_admitted_as_a_sample",
     "doc": "The summary block's ops(total) read as an instantaneous rate."
    },
    {
     "name": "test_quantile_ordering_catches_a_transposition",
     "doc": ""
    },
    {
     "name": "test_error_counter_must_not_decrease_within_a_tier",
     "doc": ""
    },
    {
     "name": "test_a_sound_run_passes_every_check",
     "doc": ""
    },
    {
     "name": "test_two_engines_differing_in_version_is_the_comparison_not_a_confound",
     "doc": "Different engines having different versions is the comparison, not a confound."
    },
    {
     "name": "test_two_runs_of_the_same_engine_still_must_match_versions",
     "doc": ""
    },
    {
     "name": "test_an_older_run_records_its_version_only_as_cockroach_version",
     "doc": "Runs written before `server_version` existed carry it in the old field;\nreading both keeps an old CockroachDB run comparable with a new one."
    },
    {
     "name": "test_unlike_machines_are_still_refused_across_engines",
     "doc": "The point of capturing hardware for PostgreSQL too: this comparison used\nto pass unexamined, because a run with no `host:` note could not be checked\nagainst one that had it."
    },
    {
     "name": "test_same_engine_cache_mismatch_still_errors",
     "doc": "Regression guard: no existing fixture put `--` flags in the server note\nat all, so `_MATCHED_SERVER_FLAGS` was never actually exercised by the\nsuite. Same-engine behaviour must be unchanged by the cross-engine fix."
    },
    {
     "name": "test_cross_engine_with_no_pg_memory_data_warns_but_does_not_error",
     "doc": "This is the state both of the project's real thesis-scale `dead` runs\nare in today -- recorded before this note existed. It must not refuse the\ncomparison, only note that cache-budget equivalence is unverified."
    },
    {
     "name": "test_cross_engine_cache_within_tolerance_is_clean",
     "doc": "A CockroachDB --cache and PostgreSQL shared_buffers within tolerance raise no error."
    },
    {
     "name": "test_cross_engine_cache_outside_tolerance_errors",
     "doc": "PostgreSQL's 128 MiB default shared_buffers against --cache=0.25 is refused."
    },
    {
     "name": "test_max_sql_memory_is_never_compared_cross_engine",
     "doc": "PostgreSQL's work_mem is per-query, not a global pool, and has no\ncounterpart to --max-sql-memory -- it must never appear in a cross-engine\nfinding, even when recorded and even when the cache axis also errors."
    }
   ]
  },
  {
   "path": "tests/test_workload_parser.py",
   "doc": "Tests for the header-bound workload parser and tick aggregation.",
   "tests": [
    {
     "name": "test_op_type_label_is_preserved",
     "doc": ""
    },
    {
     "name": "test_throughput_is_summed_across_op_types_not_averaged",
     "doc": ""
    },
    {
     "name": "test_latency_distributions_are_not_pooled",
     "doc": "Reads are leaseholder-local; updates pay cross-region quorum."
    },
    {
     "name": "test_quantiles_bind_by_header_name_not_position",
     "doc": ""
    },
    {
     "name": "test_single_op_output_is_still_labelled",
     "doc": "A read-only run labels its lines; it does not fall back to an 8-field row."
    },
    {
     "name": "test_summary_block_is_classified_not_measured",
     "doc": ""
    },
    {
     "name": "test_summary_rows_never_reach_the_tick_stream",
     "doc": ""
    },
    {
     "name": "test_summary_totals_cross_check_the_periodic_stream",
     "doc": ""
    },
    {
     "name": "test_summary_header_op_column_is_not_bound_as_a_measurement",
     "doc": "The cumulative header ends ``__total``; the periodic header does not."
    },
    {
     "name": "test_result_block_is_the_cross_op_aggregate",
     "doc": "The final ``__result`` block carries a blank op field and totals both ops."
    },
    {
     "name": "test_update_latency_is_above_the_quorum_floor",
     "doc": "A committed write cannot be faster than the follower that completes quorum."
    },
    {
     "name": "test_data_before_header_is_refused_in_strict_mode",
     "doc": ""
    },
    {
     "name": "test_unexpected_field_count_raises_rather_than_guessing",
     "doc": ""
    },
    {
     "name": "test_non_numeric_token_names_the_offending_column",
     "doc": "A future layout change must fail legibly, not as a bare ValueError."
    },
    {
     "name": "test_timed_ticks_carry_the_arrival_of_their_first_line",
     "doc": "The harness's clock is attached to each interval, not inferred from it."
    },
    {
     "name": "test_timed_ticks_discard_summary_blocks_like_group_ticks_does",
     "doc": "A cumulative total is not an interval, whichever grouper sees it."
    }
   ]
  }
 ],
 "scripts": {
  "run-experiment.sh": "run-experiment.sh: measure all four phases end to end.\n\nWith no arguments on a terminal, this launches the full two-engine pipeline\n(pipeline/run_all.py): deploy CockroachDB, measure, destroy and clean the\nTailscale devices, then the same for PostgreSQL/Patroni, then the insights.\n\nWith arguments it measures the one engine currently deployed. Preconditions:\n`terraform apply` has completed, cloud-init has finished on every node and\nTailscale is up on this machine. DB_URI is derived from --engine (override\nwith DB_URI_COCKROACHDB / DB_URI_POSTGRESQL in the environment or .env).\nIt stops at the first failed check.\n\n  ./run-experiment.sh                            # full two-engine pipeline (TUI)\n  ./run-experiment.sh --profile thesis-extended  # one engine, full sweep, ~75 min\n  ./run-experiment.sh --smoke                    # harness self-test, ~14 min\n  ./run-experiment.sh --ask                      # one engine, asks for the options\n  ./run-experiment.sh --skip-load                # working set already loaded\n  ./run-experiment.sh --no-chaos                 # phases I-II only\n  ./run-experiment.sh --engine postgresql        # measure the PostgreSQL deployment\n\n--engine names the engine currently deployed; it does not deploy anything.",
  "generate_insights.sh": "generate_insights.sh: draw the 31-chart insights catalogue from the runs on disk.\n\nNeeds no testbed. Wraps `crdblab insights`, which loads every run under runs/\nthrough the gated loader (refused runs are listed, never charted), draws charts\nA1-E4 and writes them into a fresh insights/<stamp>_<profile|all>/ beside\ninsights.md, dashboard.html, summary.json, summary.csv and chart_status.csv.\n\n  ./generate_insights.sh                            # asks which profile (on a terminal)\n  ./generate_insights.sh --profile thesis-extended  # only runs of that profile\n  ./generate_insights.sh --all                      # every profile, newest run of each kind\n  ./generate_insights.sh --out <dir>                # parent directory (default insights)\n  ./generate_insights.sh --open                     # open the dashboard when done (macOS)\n\nWithout a terminal and without --profile it renders every profile, like --all.",
  "pipeline/run_all.py": "usage: run_all.py [-h] [--profile PROFILE] [--no-chaos] [--chaos]\n                  [--engines ENGINES] [--yes]\n                  [--on-failure {ask,destroy,leave}] [--plain] [--ascii]\n                  [--cleanup] [--engine {cockroachdb,postgresql}]\n                  [--tailscale-list] [--dry-run]\n\nRun the whole two-engine experiment as one terminal UI.\n\noptions:\n  -h, --help            show this help message and exit\n  --profile PROFILE     experiment profile (asks if omitted)\n  --no-chaos            phases I-II only\n  --chaos\n  --engines ENGINES     comma-separated, in order (default:\n                        cockroachdb,postgresql)\n  --yes                 skip the confirm screen\n  --on-failure {ask,destroy,leave}\n                        what to do with running VMs when a step fails (default\n                        ask)\n  --plain               scrolling output, no full-screen UI\n  --ascii               plain-ASCII UI glyphs\n  --cleanup             only: tailscale logout, terraform destroy, delete\n                        tailnet devices\n  --engine {cockroachdb,postgresql}\n                        engine for --cleanup\n  --tailscale-list      list the tailnet devices a purge would delete, and\n                        exit\n  --dry-run             run the UI with every command faked; nothing is\n                        provisioned\n\nFor each engine in turn -- CockroachDB, then PostgreSQL/Patroni:\n\n    terraform plan -out plan.out -var=database_engine=<engine>\n    terraform apply plan.out                 (any error aborts)\n    wait for all six VMs to finish cloud-init\n    ./run-experiment.sh --engine <engine> --profile <profile>\n    tailscale logout on every VM\n    terraform destroy -auto-approve\n    delete the VMs' devices from the tailnet (Tailscale API), and verify\n\nand then ``./generate_insights.sh --profile <profile>`` plus the engine\ncomparison. DB_URI needs no editing between engines: run-experiment.sh derives\nit from --engine.\n\nIf a step fails, or you press Ctrl-C, while VMs may be up, it asks whether to\ndestroy them (and clean the tailnet) or leave them up for debugging.\n\n    ./run-experiment.sh                       # this, from a terminal\n    pipeline/run_all.py --profile smoke --yes # no setup form\n    pipeline/run_all.py --engines postgresql  # resume with the second engine\n    pipeline/run_all.py --cleanup --engine cockroachdb\n    pipeline/run_all.py --tailscale-list      # which devices would be deleted\n    pipeline/run_all.py --dry-run             # the UI, with no cloud calls\n\nA full thesis-extended run takes hours; start it inside tmux so it survives a\nclosed terminal. Everything is also written to runs/_logs/pipeline-<stamp>.log.",
  "demo.sh": "usage: replay.py [-h] [--minutes MINUTES] [--plain] [--as-recorded] [--ascii]\n                 [--no-captions]\n\nReplay the recorded thesis-extended experiment as a terminal UI, in minutes.\n\noptions:\n  -h, --help         show this help message and exit\n  --minutes MINUTES  playback length (default 4)\n  --plain            plain scrolling output, no full-screen UI\n  --as-recorded      show the logs unspliced: the CockroachDB Phase IV timeout\n                     and its re-run, and PostgreSQL's --skip-load\n  --ascii            draw the UI with plain ASCII only, for fonts missing box\n                     and block characters\n  --no-captions      hide the caption naming the run Terraform output was\n                     recorded in"
 }
};

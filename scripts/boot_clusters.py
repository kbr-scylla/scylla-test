from pathlib import Path
from typing import Optional, List, Sequence
from threading import Thread
from dataclasses import dataclass, field, replace
import argparse
import itertools
import operator
import libtmux # type: ignore
import os
import sys
import logging
import pprint

from lib.node_config import RunOpts, ClusterConfig
from lib.local_node import mk_cluster_env
from lib.tmux_node import TmuxNode
from lib.node import Node

def create_cluster(
        logger: logging.Logger,
        run_path: Path, sess: libtmux.Session, scylla_path: Path,
        ip_start: int, num_nodes: int, opts: RunOpts, cluster_cfg: ClusterConfig) -> Sequence[Node]:
    envs = mk_cluster_env(ip_start, num_nodes, opts, cluster_cfg)
    nodes = [TmuxNode(logger, run_path / e.cfg.ip_addr, e, sess, scylla_path) for e in envs]
    return nodes

def boot(nodes: Sequence[Node]) -> Thread:
    def start():
        for n in nodes:
            n.start()
    start_thread = Thread(target=start)
    start_thread.start()
    return start_thread

@dataclass(frozen=True)
class TestConfig:
    sess: libtmux.Session
    scylla_path: Path
    run_path: Path
    num_nodes: List[int] = field(default_factory=lambda:[3])
    num_shards: int = 3
    overprovisioned: bool = True
    stall_notify_ms: Optional[int] = 10
    ring_delay_ms: int = 4000
    first_node_skip_gossip_settle: bool = True
    experimental: List[str] = field(default_factory=list)
    start_clusters: bool = True
    extra_opts: str = ''
    extra_cfg: dict = field(default_factory=dict)
    ip_start: int = 1

def boot_clusters(cfg: TestConfig):
    if any(n <= 0 for n in cfg.num_nodes):
        print('Cluster sizes must be positive')
        exit(1)
    if cfg.num_shards <= 0:
        print('Number of shards must be positive')
        exit(1)
    if cfg.stall_notify_ms and cfg.stall_notify_ms <= 0:
        print('stall-notify-ms must be positive')
        exit(1)
    if cfg.ring_delay_ms < 0:
        print('ring-delay-ms must be nonnegative')
        exit(1)

    cfg.run_path.mkdir(parents=True)
    last_run = cfg.run_path.parent / 'last_boot'
    last_run.unlink(missing_ok=True)
    last_run.symlink_to(cfg.run_path)
    logging.basicConfig(
        level = logging.INFO,
        format = "%(asctime)s [%(levelname)s] %(message)s",
        handlers = [
            logging.FileHandler(cfg.run_path / 'run.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger = logging.getLogger()

    logger.info(pprint.pformat(cfg))

    opts = replace(RunOpts(),
            developer_mode = True,
            smp = cfg.num_shards,
            overprovisioned = cfg.overprovisioned,
            stall_notify_ms = cfg.stall_notify_ms,
            extra = cfg.extra_opts)

    cluster_cfg = ClusterConfig(
        ring_delay_ms = cfg.ring_delay_ms,
        first_node_skip_gossip_settle = cfg.first_node_skip_gossip_settle,
        experimental = cfg.experimental,
        extra = cfg.extra_cfg
    )

    ip_starts = itertools.accumulate([cfg.ip_start] + cfg.num_nodes, operator.add)
    logger.info('Creating {} clusters...'.format(len(cfg.num_nodes)))
    cs = [create_cluster(logger, cfg.run_path, cfg.sess, cfg.scylla_path, ip_start, num, opts, cluster_cfg)
            for ip_start, num in zip(ip_starts, cfg.num_nodes)]

    if cfg.start_clusters:
        ts = [boot(c) for c in cs]
        logger.info('Waiting for clusters to boot...')
        for t in ts: t.join()

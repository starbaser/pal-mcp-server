#!/usr/bin/env python3
"""Watch debug metrics from talkd and talkc.

This script subscribes to debug sockets published by talkd daemon and talkc client,
decodes msgpack payloads, and displays metrics in a live-updating Rich table.

Socket details:
- Daemon: $XDG_RUNTIME_DIR/talkd.debug (topic: debug.daemon)
- Client: $XDG_RUNTIME_DIR/talkc.debug (topic: debug.client)
- Encoding: topic + null byte + msgpack payload

Usage:
    uv run python scripts/debug_watch.py

Requirements:
- talkd/talkc must be running with debug_mode=true in config
- Sockets must exist in $XDG_RUNTIME_DIR

Features:
- Real-time metrics display with 10 Hz refresh rate
- Message rate calculation (msgs/sec)
- Timestamp/age of last update for each source
- Automatic reconnection on connection loss
- Graceful handling of missing sockets
- Clean exit on Ctrl+C
"""

import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import msgpack
import pynng
from rich.console import Console
from rich.live import Live
from rich.table import Table

console = Console()


def get_socket_paths() -> tuple[str, str, str, str]:
    """Get debug socket paths and file paths."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    daemon_file = f"{runtime_dir}/talkd.debug"
    client_file = f"{runtime_dir}/talkc.debug"
    return (
        f"ipc://{daemon_file}",
        f"ipc://{client_file}",
        daemon_file,
        client_file,
    )


def decode_message(msg: bytes) -> tuple[str, dict[str, Any]]:
    """Decode topic and payload from message."""
    # Split on null byte
    topic, payload = msg.split(b"\x00", 1)
    return topic.decode(), msgpack.unpackb(payload)


def format_timedelta(td: timedelta) -> str:
    """Format timedelta as human-readable string."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 1:
        return "just now"
    if total_seconds < 60:
        return f"{total_seconds}s ago"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago"


def calculate_rate(current: int, previous: int, time_diff: float) -> float:
    """Calculate rate per second."""
    if time_diff <= 0:
        return 0.0
    return (current - previous) / time_diff


class MetricsTracker:
    """Track metrics and calculate rates."""

    def __init__(self) -> None:
        self.daemon_metrics: dict[str, Any] | None = None
        self.client_metrics: dict[str, Any] | None = None
        self.daemon_last_update: datetime | None = None
        self.client_last_update: datetime | None = None
        self.daemon_prev_msgs: dict[str, int] = {}
        self.client_prev_msgs: dict[str, int] = {}
        self.last_rate_calc: datetime = datetime.now()

    def update_daemon(self, metrics: dict[str, Any]) -> None:
        """Update daemon metrics."""
        self.daemon_metrics = metrics
        self.daemon_last_update = datetime.now()

    def update_client(self, metrics: dict[str, Any]) -> None:
        """Update client metrics."""
        self.client_metrics = metrics
        self.client_last_update = datetime.now()

    def get_daemon_rates(self) -> dict[str, float]:
        """Calculate daemon message rates."""
        if not self.daemon_metrics:
            return {}

        now = datetime.now()
        time_diff = (now - self.last_rate_calc).total_seconds()

        rates = {}
        pull_msgs = self.daemon_metrics.get("pull_socket", {}).get("msgs_total", 0)
        pub_msgs = self.daemon_metrics.get("pub_socket", {}).get("msgs_total", 0)

        if "pull_msgs" in self.daemon_prev_msgs:
            rates["pull_rate"] = calculate_rate(pull_msgs, self.daemon_prev_msgs["pull_msgs"], time_diff)
        if "pub_msgs" in self.daemon_prev_msgs:
            rates["pub_rate"] = calculate_rate(pub_msgs, self.daemon_prev_msgs["pub_msgs"], time_diff)

        self.daemon_prev_msgs = {"pull_msgs": pull_msgs, "pub_msgs": pub_msgs}
        return rates

    def get_client_rates(self) -> dict[str, float]:
        """Calculate client message rates."""
        if not self.client_metrics:
            return {}

        now = datetime.now()
        time_diff = (now - self.last_rate_calc).total_seconds()

        rates = {}
        push_msgs = self.client_metrics.get("push_socket", {}).get("msgs_total", 0)
        sub_msgs = self.client_metrics.get("sub_socket", {}).get("msgs_total", 0)

        if "push_msgs" in self.client_prev_msgs:
            rates["push_rate"] = calculate_rate(push_msgs, self.client_prev_msgs["push_msgs"], time_diff)
        if "sub_msgs" in self.client_prev_msgs:
            rates["sub_rate"] = calculate_rate(sub_msgs, self.client_prev_msgs["sub_msgs"], time_diff)

        self.client_prev_msgs = {"push_msgs": push_msgs, "sub_msgs": sub_msgs}
        return rates


def create_metrics_table(tracker: MetricsTracker) -> Table:
    """Create rich table from metrics."""
    table = Table(title="Debug Metrics", show_header=True, header_style="bold magenta")
    table.add_column("Source", style="cyan", width=10)
    table.add_column("Metric", style="green", width=25)
    table.add_column("Value", style="yellow", justify="right", width=15)
    table.add_column("Rate", style="blue", justify="right", width=12)

    # Daemon metrics
    if tracker.daemon_metrics:
        daemon = tracker.daemon_metrics
        rates = tracker.get_daemon_rates()
        age = ""
        if tracker.daemon_last_update:
            age = format_timedelta(datetime.now() - tracker.daemon_last_update)

        table.add_row("daemon", "last_update", age, "", style="dim" if age != "just now" else "")

        # Pull socket metrics
        pull = daemon.get("pull_socket", {})
        table.add_row(
            "daemon",
            "pull_msgs_total",
            str(pull.get("msgs_total", 0)),
            f"{rates.get('pull_rate', 0):.1f}/s" if "pull_rate" in rates else "",
        )
        table.add_row("daemon", "pull_msgs_dropped", str(pull.get("msgs_dropped", 0)), "")

        # Pub socket metrics
        pub = daemon.get("pub_socket", {})
        table.add_row(
            "daemon",
            "pub_msgs_total",
            str(pub.get("msgs_total", 0)),
            f"{rates.get('pub_rate', 0):.1f}/s" if "pub_rate" in rates else "",
        )
        table.add_row("daemon", "pub_msgs_dropped", str(pub.get("msgs_dropped", 0)), "")

        # Session metrics
        sessions = daemon.get("sessions", {})
        table.add_row("daemon", "active_sessions", str(sessions.get("active", 0)), "")
        table.add_row("daemon", "total_sessions", str(sessions.get("total", 0)), "")

        # Memory metrics
        memory = daemon.get("memory", {})
        if memory:
            table.add_row("daemon", "heap_alloc_mb", f"{memory.get('heap_alloc', 0) / 1024 / 1024:.2f}", "")
            table.add_row("daemon", "sys_alloc_mb", f"{memory.get('sys_alloc', 0) / 1024 / 1024:.2f}", "")

    # Client metrics
    if tracker.client_metrics:
        client = tracker.client_metrics
        rates = tracker.get_client_rates()
        age = ""
        if tracker.client_last_update:
            age = format_timedelta(datetime.now() - tracker.client_last_update)

        table.add_row("client", "last_update", age, "", style="dim" if age != "just now" else "")

        # Event queue metrics
        queue = client.get("event_queue", {})
        table.add_row("client", "event_queue_depth", str(queue.get("depth", 0)), "")
        table.add_row("client", "event_queue_capacity", str(queue.get("capacity", 0)), "")

        # VAD metrics
        vad = client.get("vad", {})
        speaking = vad.get("speaking", False)
        table.add_row(
            "client",
            "vad_speaking",
            "YES" if speaking else "NO",
            "",
            style="bold green" if speaking else "",
        )
        table.add_row("client", "vad_speech_prob", f"{vad.get('speech_prob', 0):.2f}", "")

        # Push socket metrics
        push = client.get("push_socket", {})
        table.add_row(
            "client",
            "push_msgs_total",
            str(push.get("msgs_total", 0)),
            f"{rates.get('push_rate', 0):.1f}/s" if "push_rate" in rates else "",
        )
        table.add_row("client", "push_msgs_dropped", str(push.get("msgs_dropped", 0)), "")

        # Sub socket metrics
        sub = client.get("sub_socket", {})
        table.add_row(
            "client",
            "sub_msgs_total",
            str(sub.get("msgs_total", 0)),
            f"{rates.get('sub_rate', 0):.1f}/s" if "sub_rate" in rates else "",
        )
        table.add_row("client", "sub_msgs_dropped", str(sub.get("msgs_dropped", 0)), "")

        # Audio metrics
        audio = client.get("audio", {})
        if audio:
            table.add_row("client", "audio_frames_sent", str(audio.get("frames_sent", 0)), "")
            table.add_row("client", "audio_buffer_underruns", str(audio.get("buffer_underruns", 0)), "")

    return table


def main() -> int:
    """Run debug watcher."""
    daemon_path, client_path, daemon_file, client_file = get_socket_paths()

    console.print(f"[cyan]Daemon socket:[/cyan] {daemon_path}")
    console.print(f"[cyan]Client socket:[/cyan] {client_path}")
    console.print("[yellow]Waiting for debug data... (Ctrl+C to exit)[/yellow]\n")

    # Try to connect to sockets
    sockets: list[tuple[str, pynng.Sub0]] = []

    # Check if daemon socket file exists before connecting
    if os.path.exists(daemon_file):
        try:
            daemon_sock = pynng.Sub0()
            daemon_sock.subscribe(b"debug.")
            daemon_sock.dial(daemon_path, block=False)
            sockets.append(("daemon", daemon_sock))
            console.print("[green]✓ Connected to daemon debug socket[/green]")
        except (pynng.exceptions.ConnectionRefused, Exception):
            console.print("[yellow]⚠ Daemon debug socket connection failed[/yellow]")
    else:
        console.print("[yellow]⚠ Daemon debug socket not available[/yellow]")

    # Check if client socket file exists before connecting
    if os.path.exists(client_file):
        try:
            client_sock = pynng.Sub0()
            client_sock.subscribe(b"debug.")
            client_sock.dial(client_path, block=False)
            sockets.append(("client", client_sock))
            console.print("[green]✓ Connected to client debug socket[/green]")
        except (pynng.exceptions.ConnectionRefused, Exception):
            console.print("[yellow]⚠ Client debug socket connection failed[/yellow]")
    else:
        console.print("[yellow]⚠ Client debug socket not available[/yellow]")

    if not sockets:
        console.print("[red]No debug sockets available. Is talkd/talkc running with debug_mode=true?[/red]")
        return 1

    console.print()
    tracker = MetricsTracker()

    try:
        with Live(create_metrics_table(tracker), console=console, refresh_per_second=10) as live:
            last_rate_update = time.time()
            while True:
                for name, sock in sockets:
                    try:
                        msg = sock.recv(block=False)
                        _topic, data = decode_message(msg)
                        if name == "daemon":
                            tracker.update_daemon(data)
                        else:
                            tracker.update_client(data)
                    except pynng.exceptions.TryAgain:
                        pass
                    except pynng.exceptions.ConnectionShutdown:
                        console.print(f"[red]Connection to {name} socket lost[/red]")
                        # Try to reconnect
                        try:
                            sock.close()
                            new_sock = pynng.Sub0()
                            new_sock.subscribe(b"debug.")
                            new_sock.dial(daemon_path if name == "daemon" else client_path, block=False)
                            # Replace socket in list
                            sockets = [(n, s) for n, s in sockets if n != name]
                            sockets.append((name, new_sock))
                            console.print(f"[green]✓ Reconnected to {name} socket[/green]")
                        except (pynng.exceptions.ConnectionRefused, Exception):
                            console.print(f"[yellow]Failed to reconnect to {name}[/yellow]")

                # Update rate calculations every second
                now = time.time()
                if now - last_rate_update >= 1.0:
                    tracker.last_rate_calc = datetime.now()
                    last_rate_update = now

                live.update(create_metrics_table(tracker))
                time.sleep(0.02)  # 50Hz poll rate

    except KeyboardInterrupt:
        console.print("\n[cyan]Shutting down...[/cyan]")
    finally:
        for _, sock in sockets:
            sock.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

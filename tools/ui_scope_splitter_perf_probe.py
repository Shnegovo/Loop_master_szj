"""Scope splitter drag-performance probe for LoopMaster.

This simulates dragging the oscilloscope pane splitters in a live 3-pane scope.
It measures Qt event processing cost for each drag step and saves a final
screenshot so the handle thickness and restored plot state can be checked.
"""

from __future__ import annotations

import argparse
import faulthandler
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication
import pyqtgraph as pg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.pcl_theme import apply_pcl_theme  # noqa: E402
from tools.ui_resize_perf_probe import _prepare_window, _pump  # noqa: E402


def _clamped_positions(length: int, ratios: tuple[float, ...]) -> list[int]:
    if length <= 0:
        return [1]
    inset = max(80, min(220, length // 5))
    low = min(inset, max(1, length - 2))
    high = max(low + 1, length - inset)
    return [max(low, min(high, int(length * ratio))) for ratio in ratios]


def run(output_dir: Path, iterations: int) -> None:
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=False)
    output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    apply_pcl_theme(app)
    pg.setConfigOptions(
        background=(255, 255, 255),
        foreground=(83, 101, 125),
        antialias=False,
    )

    window = _prepare_window(output_dir)
    _pump(0.2)

    horizontal = _clamped_positions(
        window._scope_pane_splitter.width(),
        (0.36, 0.42, 0.50, 0.58, 0.64, 0.56, 0.48, 0.40),
    )
    vertical = _clamped_positions(
        window._scope_right_splitter.height(),
        (0.36, 0.44, 0.52, 0.60, 0.66, 0.58, 0.48, 0.40),
    )

    samples: list[tuple[int, str, int, float]] = []
    for index in range(iterations):
        if index % 2 == 0:
            splitter = window._scope_pane_splitter
            axis = "h"
            position = horizontal[(index // 2) % len(horizontal)]
        else:
            splitter = window._scope_right_splitter
            axis = "v"
            position = vertical[(index // 2) % len(vertical)]
        started = time.perf_counter()
        splitter.moveSplitter(position, 1)
        QApplication.processEvents()
        samples.append((index, axis, position, (time.perf_counter() - started) * 1000.0))

    _pump(0.25)
    screenshot = output_dir / "ui-scope-splitter-perf-final.png"
    window.grab().save(str(screenshot))
    window._on_stop()
    window.close()
    QApplication.processEvents()
    app.quit()

    durations = [sample[3] for sample in samples]
    ordered = sorted(durations)
    p95 = ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]
    p99 = ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.99) - 1))]
    avg = statistics.fmean(durations)
    worst = max(durations)
    slow = [sample for sample in samples if sample[3] > 24.0]
    top = sorted(samples, key=lambda sample: sample[3], reverse=True)[:5]
    print(
        f"splitter steps={iterations} avg={avg:.2f}ms p95={p95:.2f}ms "
        f"p99={p99:.2f}ms max={worst:.2f}ms slow>24ms={len(slow)} "
        f"top={[(i, axis, pos, round(ms, 2)) for i, axis, pos, ms in top]} "
        f"screenshot={screenshot}",
        flush=True,
    )

    if p95 > 18.0 or worst > 60.0:
        raise SystemExit("splitter performance budget exceeded")

    faulthandler.cancel_dump_traceback_later()
    sys.stdout.flush()
    sys.stderr.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ui-scope-splitter-perf")
    parser.add_argument("--iterations", type=int, default=240)
    args = parser.parse_args()
    run(args.output_dir, max(1, args.iterations))


if __name__ == "__main__":
    main()

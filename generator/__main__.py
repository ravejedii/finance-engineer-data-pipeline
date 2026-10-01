"""Generate Kiln's synthetic raw data.

    uv run python -m generator --scale small
    uv run python -m generator --scale full --seed 7 --out data
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from generator.config import SCALES
from generator.mess import apply_file_mess, plant_system_mismatches, truth_facts
from generator.render import render
from generator.simulate import simulate
from generator.write import write_all


def generate(scale_name: str, seed: int, out: Path) -> dict:
    scale = SCALES[scale_name]
    truth = simulate(seed, scale)
    issues = plant_system_mismatches(truth, seed)
    rendered = render(truth, seed)
    issues.update(apply_file_mess(rendered, truth, seed))
    issues.update(truth_facts(truth, rendered))
    meta = {"scale": scale_name, "seed": seed, "start": scale.start.isoformat(),
            "end": scale.end.isoformat()}
    stats = write_all(out, truth, rendered, issues, meta)
    stats.update({
        "sellers": len(truth.sellers),
        "orders": sum(o.in_app_db for o in truth.orders.values()),
        "refunds": len(truth.refunds),
        "disputes": len(truth.disputes),
        "seller_payouts": len(truth.seller_payouts),
        "processor_a_rows": len(rendered.a_rows),
        "processor_b_rows": len(rendered.b_rows),
        "bank_rows": len(rendered.bank_rows),
    })
    return {"stats": stats, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scale", choices=sorted(SCALES), default="small")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()
    started = time.perf_counter()
    result = generate(args.scale, args.seed, args.out)
    for key, value in result["stats"].items():
        print(f"{key:>20}: {value:,}")
    print(f"{'seconds':>20}: {time.perf_counter() - started:.1f}")


if __name__ == "__main__":
    main()

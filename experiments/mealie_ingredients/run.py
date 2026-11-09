"""Compare ingredient extraction locally or through a running Mealie server."""

# ruff: noqa: INP001

import argparse
import asyncio
from collections import Counter
import csv
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sys
from typing import Any
import unicodedata

import aiohttp

ROOT = Path(__file__).resolve().parent


def normalize(value: str) -> str:
    """Normalize casing and whitespace without removing meaningful qualifiers."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def matches(value: str, alternatives: str) -> bool:
    """Compare against explicitly accepted names, including an absent unit."""
    return normalize(value) in {normalize(item) for item in alternatives.split("|")}


def parse_local(text: str) -> dict[str, Any]:
    """Run the English model, including on Swedish inputs as a stress test."""
    from ingredient_parser import parse_ingredient  # noqa: PLC0415

    parsed = parse_ingredient(text, lang="en", string_units=True)
    amount = parsed.amount[0] if parsed.amount else None
    return {
        "food": " | ".join(name.text for name in parsed.name),
        "quantity": float(amount.quantity) if amount else None,
        "unit": str(amount.unit) if amount else "",
        "raw": asdict(parsed),
    }


async def parse_mealie(
    session: aiohttp.ClientSession, url: str, parser: str, text: str
) -> dict[str, Any]:
    """Call the parser endpoint without editing recipes or following redirects."""
    async with session.post(
        f"{url.rstrip('/')}/api/parser/ingredient",
        json={"parser": parser, "ingredient": text},
        allow_redirects=False,
    ) as response:
        if response.status != 200:
            return {"error": f"HTTP {response.status}"}
        raw = await response.json()
    ingredient = raw["ingredient"]
    return {
        "food": (ingredient["food"] or {}).get("name", ""),
        "quantity": ingredient["quantity"],
        "unit": (ingredient["unit"] or {}).get("name", ""),
        "raw": raw,
    }


async def run(args: argparse.Namespace, cases: list[dict[str, str]]) -> int:
    """Save raw responses, field comparisons, metadata, and a manual review sheet."""
    results = []
    headers = {}
    if args.backend == "mealie":
        headers["Authorization"] = f"Bearer {os.environ['MEALIE_TOKEN']}"
    async with aiohttp.ClientSession(
        headers=headers, timeout=aiohttp.ClientTimeout(total=60)
    ) as session:
        for case in cases:
            try:
                if args.backend == "local":
                    result = parse_local(case["text"])
                else:
                    result = await parse_mealie(
                        session, args.url, args.parser, case["text"]
                    )
            except aiohttp.ClientError, TimeoutError:
                result = {"error": "Connection failed or timed out"}
            results.append({"case": case, "result": result})

    metadata = {
        "created_at": datetime.now(UTC).isoformat(),
        "backend": args.backend,
        "parser": args.parser if args.backend == "mealie" else "English NLP model",
        "version": args.mealie_version
        if args.backend == "mealie"
        else version("ingredient-parser-nlp"),
        "python": sys.version,
        "input_type": "products" if args.products else "ingredients",
    }
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "responses.json").write_text(
        json.dumps(
            {"metadata": metadata, "results": results},
            indent=2,
            default=str,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.products:
        return write_product_review(args.output, metadata, results)
    counts: dict[str, Counter] = {language: Counter() for language in ("sv", "en")}
    with (args.output / "review.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id",
                "language",
                "text",
                "expected_food",
                "actual_food",
                "expected_quantity",
                "actual_quantity",
                "expected_unit",
                "actual_unit",
                "food_ok",
                "quantity_ok",
                "unit_ok",
                "error",
                "required_qualifiers",
                "qualifiers_preserved_manual",
                "notes",
            ],
        )
        writer.writeheader()
        for entry in results:
            case, result = entry["case"], entry["result"]
            count = counts[case["language"]]
            count["cases"] += 1
            error = result.get("error", "")
            count["errors"] += bool(error)
            food_ok = not error and matches(result["food"], case["food_names"])
            unit_ok = not error and matches(result["unit"], case["unit_names"])
            quantity_ok: bool | str = ""
            if case["quantity"]:
                count["quantity_cases"] += 1
                quantity_ok = (
                    not error
                    and result["quantity"] is not None
                    and math.isclose(
                        float(result["quantity"]),
                        float(case["quantity"]),
                        abs_tol=0.001,
                    )
                )
                count["quantity_correct"] += bool(quantity_ok)
            count["food_correct"] += food_ok
            count["unit_correct"] += unit_ok
            writer.writerow(
                {
                    "id": case["id"],
                    "language": case["language"],
                    "text": case["text"],
                    "expected_food": case["food_names"],
                    "actual_food": result.get("food", ""),
                    "expected_quantity": case["quantity"],
                    "actual_quantity": result.get("quantity", ""),
                    "expected_unit": case["unit_names"],
                    "actual_unit": result.get("unit", ""),
                    "food_ok": food_ok,
                    "quantity_ok": quantity_ok,
                    "unit_ok": unit_ok,
                    "error": error,
                    "required_qualifiers": case["qualifiers"],
                    "qualifiers_preserved_manual": "",
                    "notes": case["notes"],
                }
            )
    summary = {"metadata": metadata, "counts_by_language": counts}
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return int(any(count["errors"] for count in counts.values()))


def write_product_review(
    output: Path, metadata: dict[str, Any], results: list[dict[str, Any]]
) -> int:
    """Report product extraction without treating recipe amounts as package labels."""
    counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    with (output / "review.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id",
                "ingredient_id",
                "ingredient_text",
                "product_name",
                "brand",
                "package_size",
                "expected_food",
                "expected_quantity",
                "expected_unit",
                "actual_food",
                "actual_quantity",
                "actual_unit",
                "food_ok",
                "quantity_ok",
                "unit_ok",
                "all_fields_ok",
                "error",
                "brand_removed_manual",
                "qualifiers_preserved_manual",
                "package_size_correct_manual",
                "reason",
            ],
        )
        writer.writeheader()
        for entry in results:
            case, result = entry["case"], entry["result"]
            error = bool(result.get("error"))
            food_ok = not error and matches(result["food"], case["expected_food_names"])
            quantity_ok: bool | str = ""
            if case["expected_quantity"]:
                quantity_ok = (
                    not error
                    and result["quantity"] is not None
                    and math.isclose(
                        float(result["quantity"]),
                        float(case["expected_quantity"]),
                        abs_tol=0.001,
                    )
                )
            unit_ok: bool | str = ""
            if case["expected_unit_names"]:
                unit_ok = not error and matches(
                    result["unit"], case["expected_unit_names"]
                )
            checks = {
                "food_ok": food_ok,
                "quantity_ok": quantity_ok,
                "unit_ok": unit_ok,
                "all_fields_ok": bool(food_ok and quantity_ok and unit_ok)
                if case["expected_quantity"] and case["expected_unit_names"]
                else "",
            }
            for key, value in checks.items():
                if value != "":
                    totals[key] += 1
                    counts[key] += int(value)
            writer.writerow(
                {
                    "id": case["id"],
                    **{
                        key: case[key]
                        for key in (
                            "ingredient_id",
                            "ingredient_text",
                            "product_name",
                            "brand",
                            "package_size",
                            "reason",
                        )
                    },
                    "actual_food": result.get("food", ""),
                    "actual_quantity": result.get("quantity", ""),
                    "actual_unit": result.get("unit", ""),
                    "expected_food": case["expected_food_names"],
                    "expected_quantity": case["expected_quantity"],
                    "expected_unit": case["expected_unit_names"],
                    **checks,
                    "error": result.get("error", ""),
                }
            )
    errors = sum("error" in entry["result"] for entry in results)
    summary = {
        "metadata": metadata,
        "products": len(results),
        "errors": errors,
        "accuracy": {
            key.removesuffix("_ok"): {
                "correct": count,
                "total": totals[key],
                "percent": round(100 * count / totals[key], 1),
            }
            for key, count in counts.items()
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return int(bool(errors))


def main() -> int:
    """Configure a reproducible extraction benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("local", "mealie"), default="local")
    parser.add_argument("--parser", choices=("nlp", "brute"), default="nlp")
    parser.add_argument("--url", help="Mealie base URL, including any subpath")
    parser.add_argument("--mealie-version", help="Version shown by your Mealie server")
    parser.add_argument("--dataset", type=Path, default=ROOT / "ingredients.csv")
    parser.add_argument(
        "--products",
        type=Path,
        help="Parse nonempty product_name fields from a product worksheet instead",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--rescore",
        type=Path,
        help="Score saved product responses.json against current labels without requests",
    )
    parser.add_argument("--validate", action="store_true", help="Check dataset only")
    args = parser.parse_args()
    input_path = args.products or args.dataset
    with input_path.open(encoding="utf-8", newline="") as file:
        cases = list(csv.DictReader(file))
    if args.products:
        cases = [
            {**case, "id": f"product-{index}", "text": case["product_name"]}
            for index, case in enumerate(cases, start=1)
            if case["product_name"].strip()
        ]
    ids = [case["id"] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        parser.error("Dataset must contain cases with unique IDs")
    for case in cases:
        if args.products:
            if not case["expected_food_names"]:
                parser.error(f"Missing product labels: {case['id']}")
            if case["expected_quantity"]:
                quantity = float(case["expected_quantity"])
                if not math.isfinite(quantity) or quantity <= 0:
                    parser.error(f"Invalid package quantity: {case['id']}")
            continue
        if case["language"] not in {"sv", "en"} or not case["food_names"]:
            parser.error(f"Invalid language or missing expected food: {case['id']}")
        if case["quantity"]:
            float(case["quantity"])
    if args.validate:
        kind = "products" if args.products else "ingredients"
        sys.stdout.write(f"Validated {len(cases)} {kind}.\n")
        return 0
    if args.output is None:
        parser.error("--output is required; use a new directory for each run")
    if args.output.exists():
        parser.error("Output directory already exists; choose a new directory")
    if args.rescore:
        if not args.products:
            parser.error("--rescore requires --products")
        saved = json.loads(args.rescore.read_text(encoding="utf-8"))
        if saved["metadata"]["input_type"] != "products":
            parser.error("Saved responses must be from product mode")
        entries = saved["results"]
        if len(entries) != len(cases) or any(
            entry["case"]["text"] != case["text"]
            or entry["case"]["ingredient_id"] != case["ingredient_id"]
            for entry, case in zip(entries, cases, strict=False)
        ):
            parser.error(
                "Saved product titles/order do not match the current worksheet"
            )
        for entry, case in zip(entries, cases, strict=True):
            entry["case"] = case
        saved["metadata"]["scored_at"] = datetime.now(UTC).isoformat()
        saved["metadata"]["source_responses"] = str(args.rescore)
        args.output.mkdir(parents=True)
        (args.output / "responses.json").write_text(
            json.dumps(saved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return write_product_review(args.output, saved["metadata"], entries)
    if args.backend == "mealie" and not (
        args.url and args.mealie_version and os.environ.get("MEALIE_TOKEN")
    ):
        parser.error("Mealie mode requires --url, --mealie-version and MEALIE_TOKEN")
    return asyncio.run(run(args, cases))


if __name__ == "__main__":
    sys.exit(main())

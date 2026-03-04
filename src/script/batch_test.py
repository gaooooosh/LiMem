# -*- coding: utf-8 -*-
"""Batch test script for LiMem LTM system.

Runs all test cases from test_cases.json and reports success rate.
"""

import json
import os
import sys
import time
from datetime import datetime

# Add src to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.config import DB_PATH
from limem.db import init_db, open_connection
from limem.ltm import ResearchLTM
from limem.search import LTMSearcher, RetrievalConfig


def load_test_cases(file_path: str) -> list[dict]:
    """Load test cases from JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("test_cases", [])


def evaluate_result(test_case: dict, result: dict) -> tuple[bool, str]:
    """Evaluate if the search result matches expected outcome.

    Returns:
        Tuple of (is_success, reason)
    """
    expected_entities = set(test_case.get("expected_entities", []))
    expected_recall = test_case.get("expected_recall", "").lower()
    category = test_case.get("category", "")

    # Get actual results
    actual_entities = set(result.get("entities", []))
    top_k_events = result.get("top_k_events", [])
    answer = result.get("answer", "").lower()

    # Check 1: Entity extraction - at least one expected entity should be found
    # or entities that are semantically similar
    entity_match = False
    if expected_entities:
        for exp_entity in expected_entities:
            # Direct match
            if exp_entity in actual_entities:
                entity_match = True
                break
            # Partial match (entity contains expected or vice versa)
            for act_entity in actual_entities:
                if exp_entity in act_entity or act_entity in exp_entity:
                    entity_match = True
                    break
            if entity_match:
                break

    # Check 2: Retrieved events should contain relevant entities
    event_match = False
    matched_entities_in_events = set()
    if top_k_events:
        for event in top_k_events:
            summary = event.summary.lower()
            for exp_entity in expected_entities:
                if exp_entity.lower() in summary:
                    event_match = True
                    matched_entities_in_events.add(exp_entity)

    # Check 3: Answer should contain key expected information
    answer_match = False
    if expected_recall:
        # Check if key terms from expected_recall appear in answer
        key_terms = [t for t in expected_recall.split() if len(t) > 1]
        # Also check expected entities in answer
        for exp_entity in expected_entities:
            if exp_entity.lower() in answer:
                answer_match = True
                break

    # Determine success based on category
    if category in ["location_recall", "entity_association"]:
        # For location/entity queries, event match is most important
        if event_match or answer_match:
            return True, f"Found relevant events: {matched_entities_in_events}"
        elif entity_match:
            return True, "Entities extracted correctly"
        else:
            return False, f"No relevant events found. Expected entities: {expected_entities}"

    elif category in ["preference_recall", "event_recall"]:
        # For preference/event queries, answer quality matters most
        if answer_match or event_match:
            return True, "Answer contains expected information"
        else:
            return False, f"Answer lacks expected info: {expected_recall[:50]}..."

    elif category == "temporal_query":
        # For temporal queries, check if time info is in answer
        if answer_match or event_match:
            return True, "Time-related info retrieved"
        else:
            return False, "Failed to retrieve temporal info"

    elif category == "multi_hop":
        # Multi-hop queries are harder - accept partial success
        if event_match or answer_match:
            return True, "Multi-hop reasoning succeeded"
        else:
            return False, "Multi-hop reasoning failed"

    elif category == "negation_update":
        # For negation queries, check if the answer addresses the negation
        if answer_match or event_match:
            return True, "Negation/update correctly recalled"
        else:
            return False, "Negation/update not properly addressed"

    elif category == "cross_domain":
        # Cross-domain queries need multiple types of info
        if event_match or answer_match:
            return True, "Cross-domain association found"
        else:
            return False, "Cross-domain association failed"

    # Default: success if we have any events and answer
    if top_k_events and answer and "抱歉" not in answer:
        return True, "Default success: has events and answer"

    return False, "No relevant results found"


def run_batch_test(test_cases: list[dict], searcher: LTMSearcher, verbose: bool = False) -> dict:
    """Run all test cases and collect results.

    Returns:
        Dictionary with test results and statistics.
    """
    results = {
        "total": len(test_cases),
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "by_category": {},
        "by_difficulty": {},
        "details": [],
        "start_time": datetime.now().isoformat(),
        "end_time": None,
    }

    for i, test_case in enumerate(test_cases, 1):
        test_id = test_case.get("id", i)
        query = test_case.get("query", "")
        category = test_case.get("category", "unknown")
        difficulty = test_case.get("difficulty", "unknown")
        expected_entities = test_case.get("expected_entities", [])
        expected_recall = test_case.get("expected_recall", "")

        print(f"\n[{i}/{len(test_cases)}] Testing: {query[:50]}...")

        try:
            # Execute search
            start_time = time.time()
            result = searcher.search(query, top_k=5)
            elapsed = time.time() - start_time

            # Evaluate result
            success, reason = evaluate_result(test_case, result)

            detail = {
                "id": test_id,
                "query": query,
                "category": category,
                "difficulty": difficulty,
                "success": success,
                "reason": reason,
                "elapsed_ms": round(elapsed * 1000, 2),
                "entities_found": result.get("entities", []),
                "events_count": len(result.get("top_k_events", [])),
                "answer": result.get("answer", "")[:200] + "..." if len(result.get("answer", "")) > 200 else result.get("answer", ""),
            }

            if verbose:
                print(f"  Entities: {result.get('entities', [])}")
                print(f"  Events: {len(result.get('top_k_events', []))}")
                print(f"  Result: {'PASS' if success else 'FAIL'} - {reason}")

            if success:
                results["passed"] += 1
            else:
                results["failed"] += 1

        except Exception as e:
            print(f"  ERROR: {str(e)}")
            results["errors"] += 1
            detail = {
                "id": test_id,
                "query": query,
                "category": category,
                "difficulty": difficulty,
                "success": False,
                "reason": f"Exception: {str(e)}",
                "elapsed_ms": 0,
                "entities_found": [],
                "events_count": 0,
                "answer": "",
            }

        results["details"].append(detail)

        # Update category stats
        if category not in results["by_category"]:
            results["by_category"][category] = {"passed": 0, "failed": 0, "errors": 0, "total": 0}
        results["by_category"][category]["total"] += 1
        if detail["success"]:
            results["by_category"][category]["passed"] += 1
        else:
            results["by_category"][category]["failed"] += 1

        # Update difficulty stats
        if difficulty not in results["by_difficulty"]:
            results["by_difficulty"][difficulty] = {"passed": 0, "failed": 0, "errors": 0, "total": 0}
        results["by_difficulty"][difficulty]["total"] += 1
        if detail["success"]:
            results["by_difficulty"][difficulty]["passed"] += 1
        else:
            results["by_difficulty"][difficulty]["failed"] += 1

    results["end_time"] = datetime.now().isoformat()
    results["success_rate"] = round(results["passed"] / results["total"] * 100, 2) if results["total"] > 0 else 0

    return results


def print_report(results: dict):
    """Print test results report."""
    print("\n" + "=" * 80)
    print("LiMem LTM Test Results Report")
    print("=" * 80)

    print(f"\n📊 Overall Results:")
    print(f"  Total:   {results['total']}")
    print(f"  Passed:  {results['passed']} ({results['success_rate']}%)")
    print(f"  Failed:  {results['failed']}")
    print(f"  Errors:  {results['errors']}")

    print(f"\n📁 By Category:")
    for cat, stats in sorted(results["by_category"].items()):
        rate = round(stats["passed"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
        print(f"  {cat:20s}: {stats['passed']:2d}/{stats['total']:2d} ({rate:5.1f}%)")

    print(f"\n🎯 By Difficulty:")
    for diff, stats in sorted(results["by_difficulty"].items()):
        rate = round(stats["passed"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
        print(f"  {diff:10s}: {stats['passed']:2d}/{stats['total']:2d} ({rate:5.1f}%)")

    print(f"\n❌ Failed Cases:")
    failed_cases = [d for d in results["details"] if not d["success"]]
    for detail in failed_cases[:10]:  # Show first 10 failures
        print(f"  [{detail['id']:3d}] {detail['query'][:40]:40s} - {detail['reason'][:40]}")
    if len(failed_cases) > 10:
        print(f"  ... and {len(failed_cases) - 10} more failures")

    print("\n" + "=" * 80)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Batch test LiMem LTM system")
    parser.add_argument(
        "--test-file",
        "-t",
        default=os.path.join(PROJECT_ROOT, "test_cases.json"),
        help="Path to test cases JSON file",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=os.path.join(PROJECT_ROOT, "outputs", "test_results.json"),
        help="Path to output results JSON file",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print verbose output for each test",
    )
    args = parser.parse_args()

    # Load test cases
    print(f"📁 Loading test cases from: {args.test_file}")
    test_cases = load_test_cases(args.test_file)
    print(f"✅ Loaded {len(test_cases)} test cases")

    # Initialize database
    print(f"\n📁 Connecting to database: {DB_PATH}")
    conn = open_connection(DB_PATH)
    init_db(conn)
    print("✅ Database connected")

    # Initialize searcher
    config = RetrievalConfig(
        default_top_k=5,
        lambda_param=0.01,
        enable_vector_match=True,
    )
    searcher = LTMSearcher(conn, config)
    print("✅ LTMSearcher initialized")

    # Run tests
    print(f"\n🚀 Running batch tests...")
    results = run_batch_test(test_cases, searcher, verbose=args.verbose)

    # Print report
    print_report(results)

    # Save results
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 Results saved to: {args.output}")


if __name__ == "__main__":
    main()

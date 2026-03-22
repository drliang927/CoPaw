#!/usr/bin/env python3
"""Test script for log variable search functionality."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from copaw.agents.tools.log_variable_search import (
    LogVariableSearcher,
    search_log_variable,
)


def test_load_logs():
    """Test loading log files."""
    print("=" * 60)
    print("Test 1: Load Logs")
    print("=" * 60)

    searcher = LogVariableSearcher(log_dir="rcalogs_processed")
    searcher.load_logs()

    print(f"Total entries loaded: {len(searcher._all_entries)}")
    print(f"Files cached: {len(searcher._log_cache)}")

    # Show first few entries
    print("\nFirst 5 entries:")
    for entry in searcher._all_entries[:5]:
        print(f"  [{entry.log_level}] {entry.step_name}: {entry.content[:50]}...")

    return searcher


def test_search_variable(searcher: LogVariableSearcher):
    """Test searching for a specific variable."""
    print("\n" + "=" * 60)
    print("Test 2: Search Variable")
    print("=" * 60)

    # Search for stfSeq variable
    results = searcher.search_variable("stfSeq", max_results=3)

    print(f"Found {len(results)} result(s) for 'stfSeq'")

    for i, result in enumerate(results, 1):
        print(f"\n--- Result {i} ---")
        print(f"Variable: {result.variable_name}")
        print(f"Value: {result.variable_value}")
        print(f"Step: {result.log_entry.step_name}")
        print(f"Time: {result.log_entry.create_time}")
        print(f"TreeId: {result.log_entry.tree_id}")

        if result.context_before:
            print(f"Context Before: {len(result.context_before)} entries")
        if result.context_after:
            print(f"Context After: {len(result.context_after)} entries")


def test_search_by_step(searcher: LogVariableSearcher):
    """Test searching by step name."""
    print("\n" + "=" * 60)
    print("Test 3: Search By Step Name")
    print("=" * 60)

    # Search for steps containing "校验"
    results = searcher.search_by_step_name("校验", max_results=3)

    print(f"Found {len(results)} step fragment(s) for '校验'")

    for i, fragment in enumerate(results, 1):
        print(f"\n--- Fragment {i} ---")
        print(f"Step Name: {fragment.step_name}")
        print(f"Time Range: {fragment.start_time} - {fragment.end_time}")
        print(f"Entries: {len(fragment.entries)}")


def test_search_by_content(searcher: LogVariableSearcher):
    """Test searching by content pattern."""
    print("\n" + "=" * 60)
    print("Test 4: Search By Content")
    print("=" * 60)

    # Search for ERROR entries
    results = searcher.search_by_content("ERROR|失败", max_results=5)

    print(f"Found {len(results)} entries matching 'ERROR|失败'")

    for i, entry in enumerate(results, 1):
        print(f"\n--- Entry {i} ---")
        print(f"Step: {entry.step_name}")
        print(f"Time: {entry.create_time}")
        print(f"Content: {entry.content[:100]}...")


def test_get_all_variables(searcher: LogVariableSearcher):
    """Test extracting all variables."""
    print("\n" + "=" * 60)
    print("Test 5: Get All Variables")
    print("=" * 60)

    variables = searcher.get_all_variables()

    print(f"Total unique variables: {len(variables)}")

    # Show first 10 variables
    print("\nFirst 10 variables:")
    for i, (var_name, assignments) in enumerate(list(variables.items())[:10], 1):
        print(f"  {i}. {var_name}: {len(assignments)} assignment(s)")
        if assignments:
            first = assignments[0]
            print(f"     First value: {first.variable_value[:50]}...")


def test_sliding_window(searcher: LogVariableSearcher):
    """Test sliding window fallback."""
    print("\n" + "=" * 60)
    print("Test 6: Sliding Window Fallback")
    print("=" * 60)

    # Search for a variable that might not exist exactly
    # This should trigger the sliding window search
    results = searcher.search_variable("nonexistent_var_xyz", max_results=3)

    print(f"Found {len(results)} result(s) for 'nonexistent_var_xyz'")
    print("(Should be 0 since this variable doesn't exist)")


def test_step_fragment_by_tree_id(searcher: LogVariableSearcher):
    """Test getting step fragment by tree ID."""
    print("\n" + "=" * 60)
    print("Test 7: Get Step Fragment By Tree ID")
    print("=" * 60)

    # Get fragment for tree_id = 0 (usually the first step)
    fragment = searcher.get_step_fragment_by_tree_id(0)

    if fragment:
        print(f"Found fragment for tree_id=0:")
        print(f"  Step Name: {fragment.step_name}")
        print(f"  Time Range: {fragment.start_time} - {fragment.end_time}")
        print(f"  Total Entries: {len(fragment.entries)}")

        # Show entries
        print("\n  Entries:")
        for entry in fragment.entries[:5]:
            print(f"    [{entry.log_level}] {entry.content[:60]}...")
    else:
        print("No fragment found for tree_id=0")


def test_sub_step_fragments(searcher: LogVariableSearcher):
    """Test getting sub-step fragments."""
    print("\n" + "=" * 60)
    print("Test 8: Get Sub-Step Fragments")
    print("=" * 60)

    # Get all sub-step fragments
    fragments = searcher.get_sub_step_fragments(max_results=10)

    print(f"Found {len(fragments)} sub-step fragments")

    for i, frag in enumerate(fragments, 1):
        print(f"\n--- Fragment {i} ---")
        print(f"  Step: {frag.step_name}")
        print(f"  TreeId: {frag.tree_id}, SubStepIndex: {frag.sub_step_index}")
        print(f"  Depth: {frag.depth}")
        print(f"  Time: {frag.start_time} - {frag.end_time}")
        print(f"  Entries: {len(frag.entries)}")


def test_variable_in_sub_step(searcher: LogVariableSearcher):
    """Test searching for variable within a specific step."""
    print("\n" + "=" * 60)
    print("Test 9: Search Variable In Sub-Step")
    print("=" * 60)

    # Get a tree_id first
    fragments = searcher.get_sub_step_fragments(max_results=1)
    if fragments:
        tree_id = fragments[0].tree_id
        print(f"Searching for 'stfSeq' in tree_id={tree_id}")

        results = searcher.search_variable_in_sub_step(
            variable_name="stfSeq",
            tree_id=tree_id,
        )

        print(f"Found {len(results)} result(s)")

        for i, result in enumerate(results, 1):
            print(f"\n--- Result {i} ---")
            print(f"  Variable: {result.variable_name}")
            print(f"  Value: {result.variable_value}")
            print(f"  Context Before: {len(result.context_before)} entries")
            print(f"  Context After: {len(result.context_after)} entries")
    else:
        print("No sub-step fragments found")


def test_execution_path(searcher: LogVariableSearcher):
    """Test getting execution path."""
    print("\n" + "=" * 60)
    print("Test 10: Get Execution Path")
    print("=" * 60)

    # Get a child step to trace its path
    # Find a step with a parent
    for entry in searcher._all_entries:
        if entry.parent_tree_id >= 0 and entry.tree_id != entry.parent_tree_id:
            tree_id = entry.tree_id
            print(f"Tracing execution path for tree_id={tree_id}")
            print(f"  (parent_tree_id={entry.parent_tree_id})")

            path = searcher.get_execution_path(tree_id)

            print(f"\nExecution path ({len(path)} steps):")
            for i, frag in enumerate(path, 1):
                print(f"  {i}. [{frag.tree_id}] {frag.step_name}")
                print(f"     Time: {frag.start_time}")

            break
    else:
        print("No child steps found to trace execution path")


def main():
    """Run all tests."""
    print("Testing Log Variable Search Module")
    print("=" * 60)

    try:
        # Test 1: Load logs
        searcher = test_load_logs()

        if not searcher._all_entries:
            print("\nNo log entries found. Please ensure rcalogs_processed directory exists.")
            return

        # Test 2: Search variable
        test_search_variable(searcher)

        # Test 3: Search by step
        test_search_by_step(searcher)

        # Test 4: Search by content
        test_search_by_content(searcher)

        # Test 5: Get all variables
        test_get_all_variables(searcher)

        # Test 6: Sliding window
        test_sliding_window(searcher)

        # Test 7: Step fragment by tree ID
        test_step_fragment_by_tree_id(searcher)

        # Test 8: Sub-step fragments
        test_sub_step_fragments(searcher)

        # Test 9: Variable in sub-step
        test_variable_in_sub_step(searcher)

        # Test 10: Execution path
        test_execution_path(searcher)

        print("\n" + "=" * 60)
        print("All tests completed!")
        print("=" * 60)

    except FileNotFoundError as e:
        print(f"\nError: {e}")
        print("Please run 'python process_rcalogs.py' first to generate processed logs.")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

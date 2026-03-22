# -*- coding: utf-8 -*-
"""Log variable search tool for finding variable assignments in RCA logs.

This module provides functionality to search for variable assignments in RCA log files
with the following features:
1. Search based on stepLog sub-steps as the minimum recall unit
2. Maintain temporal logic: stepChildren -> stepLog -> sub-steps
3. Sliding window as fallback mechanism at sub-step level

Log Structure:
- Root step has: stepName, treeId, status, action, stepLog, stepChildren
- stepLog is a list of log entries, each with traceId, content, logLevel, createTime
- stepChildren recursively contains child steps
- Variable format: ${$.BODY_DATA.stfSeq} or ${res_data}
"""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock


@dataclass
class LogEntry:
    """Represents a single log entry in stepLog."""
    trace_id: int
    content: str
    log_level: str
    create_time: str
    step_name: str = ""
    tree_id: int = -1
    parent_tree_id: int = -1
    depth: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "traceId": self.trace_id,
            "content": self.content,
            "logLevel": self.log_level,
            "createTime": self.create_time,
            "stepName": self.step_name,
            "treeId": self.tree_id,
            "parentTreeId": self.parent_tree_id,
            "depth": self.depth,
        }


@dataclass
class VariableAssignment:
    """Represents a variable assignment found in logs."""
    variable_name: str
    variable_value: str
    log_entry: LogEntry
    context_before: list[LogEntry] = field(default_factory=list)
    context_after: list[LogEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "variableName": self.variable_name,
            "variableValue": self.variable_value,
            "logEntry": self.log_entry.to_dict(),
            "contextBefore": [e.to_dict() for e in self.context_before],
            "contextAfter": [e.to_dict() for e in self.context_after],
        }


@dataclass
class StepFragment:
    """Represents a fragment of execution steps for sliding window.
    
    This corresponds to a stepLog's sub-step level granularity.
    """
    entries: list[LogEntry] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""
    step_name: str = ""
    tree_id: int = -1
    depth: int = 0
    # Sub-step index within the stepLog
    sub_step_index: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "entries": [e.to_dict() for e in self.entries],
            "startTime": self.start_time,
            "endTime": self.end_time,
            "stepName": self.step_name,
            "treeId": self.tree_id,
            "depth": self.depth,
            "subStepIndex": self.sub_step_index,
        }


class LogVariableSearcher:
    """Search for variable assignments in RCA log files.

    This class provides methods to:
    1. Parse log files into structured format
    2. Extract variable assignments from log content
    3. Search for specific variables with context
    4. Apply sliding window as fallback mechanism
    """

    # Patterns for extracting variable assignments
    # Pattern 1: 组件入参：【${var}】 = 【value】
    INPUT_PARAM_PATTERN = re.compile(
        r"组件入参：【(\$\{[^}]+\})】\s*=\s*【(.+?)】"
    )
    # Pattern 2: 解析组件入参变量【${var}】=【value】
    PARSE_INPUT_PATTERN = re.compile(
        r"解析组件入参变量【(\$\{[^}]+\})】=【(.+?)】"
    )
    # Pattern 3: 解析python返回变量【${var}】=【value】
    RETURN_VAR_PATTERN = re.compile(
        r"解析python返回变量【(\$\{[^}]+\})】=【(.+?)】"
    )
    # Pattern 4: 将【value】赋值给【${var}】
    ASSIGN_VAR_PATTERN = re.compile(
        r"将【(.+?)】赋值给【(\$\{[^}]+\})】"
    )
    # Pattern 5: Simple variable format without ${}
    SIMPLE_VAR_PATTERN = re.compile(
        r"【(\$\{[^}]+\})】\s*=\s*【(.+?)】"
    )

    # Window size for context (number of entries before/after)
    DEFAULT_CONTEXT_WINDOW = 3
    # Sliding window size (number of stepLog entries)
    DEFAULT_SLIDING_WINDOW_SIZE = 10

    def __init__(
        self,
        log_dir: str = "rcalogs_processed",
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        sliding_window_size: int = DEFAULT_SLIDING_WINDOW_SIZE,
    ):
        """Initialize the log variable searcher.

        Args:
            log_dir: Directory containing processed log files
            context_window: Number of entries to include as context before/after
            sliding_window_size: Size of sliding window for fallback search
        """
        self.log_dir = Path(log_dir)
        self.context_window = context_window
        self.sliding_window_size = sliding_window_size
        self._log_cache: dict[str, list[dict]] = {}
        self._all_entries: list[LogEntry] = []

    def load_logs(self) -> None:
        """Load all log files from the log directory."""
        if not self.log_dir.exists():
            raise FileNotFoundError(f"Log directory not found: {self.log_dir}")

        json_files = sorted(self.log_dir.glob("*.json"))
        for json_file in json_files:
            self._load_log_file(json_file)

        # Sort all entries by time
        self._all_entries.sort(key=lambda e: e.create_time)

    def _load_log_file(self, file_path: Path) -> None:
        """Load a single log file and extract all entries."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            file_name = file_path.name
            self._log_cache[file_name] = data

            # Extract all entries with hierarchy info
            for entry in data:
                self._extract_entries_from_step(entry, depth=0, parent_tree_id=-1)

        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load {file_path}: {e}")

    def _extract_entries_from_step(
        self,
        step: dict,
        depth: int,
        parent_tree_id: int,
    ) -> None:
        """Extract log entries from a step and its children recursively.

        This maintains the temporal logic: stepChildren -> stepLog -> sub-steps

        Args:
            step: Step dictionary containing stepLog and stepChildren
            depth: Current depth in the hierarchy
            parent_tree_id: Tree ID of the parent step
        """
        tree_id = step.get("treeId", -1)
        step_name = step.get("stepName", "")

        # Extract entries from stepLog
        for log_entry in step.get("stepLog", []):
            entry = LogEntry(
                trace_id=log_entry.get("traceId", -1),
                content=log_entry.get("content", ""),
                log_level=log_entry.get("logLevel", ""),
                create_time=log_entry.get("createTime", ""),
                step_name=step_name,
                tree_id=tree_id,
                parent_tree_id=parent_tree_id,
                depth=depth,
            )
            self._all_entries.append(entry)

        # Recursively process stepChildren
        for child in step.get("stepChildren", []):
            self._extract_entries_from_step(
                child,
                depth=depth + 1,
                parent_tree_id=tree_id,
            )

    def extract_variable_assignments(
        self,
        entry: LogEntry,
    ) -> list[VariableAssignment]:
        """Extract all variable assignments from a log entry.

        Args:
            entry: Log entry to extract variables from

        Returns:
            List of VariableAssignment objects found in the entry
        """
        content = entry.content
        assignments = []

        # Try all patterns
        patterns = [
            self.INPUT_PARAM_PATTERN,
            self.PARSE_INPUT_PATTERN,
            self.RETURN_VAR_PATTERN,
            self.SIMPLE_VAR_PATTERN,
        ]

        for pattern in patterns:
            matches = pattern.findall(content)
            for match in matches:
                if len(match) == 2:
                    var_name, var_value = match
                    assignments.append(VariableAssignment(
                        variable_name=var_name,
                        variable_value=var_value,
                        log_entry=entry,
                    ))

        # Handle assign pattern separately (different order)
        matches = self.ASSIGN_VAR_PATTERN.findall(content)
        for match in matches:
            if len(match) == 2:
                var_value, var_name = match
                assignments.append(VariableAssignment(
                    variable_name=var_name,
                    variable_value=var_value,
                    log_entry=entry,
                ))

        return assignments

    def search_variable(
        self,
        variable_name: str,
        max_results: int = 5,
        include_context: bool = True,
    ) -> list[VariableAssignment]:
        """Search for a specific variable assignment.

        First tries to find exact matches in stepLog entries, maintaining
        the temporal order. If no results found, uses sliding window as fallback.

        Args:
            variable_name: Variable name to search for (can be partial)
            max_results: Maximum number of results to return
            include_context: Whether to include context entries

        Returns:
            List of VariableAssignment objects matching the search
        """
        if not self._all_entries:
            self.load_logs()

        # Normalize variable name for matching
        search_var = variable_name.strip()
        if not search_var.startswith("${"):
            search_var = "${" + search_var
        if not search_var.endswith("}"):
            search_var = search_var + "}"

        results = []

        # Search through all entries in temporal order
        for i, entry in enumerate(self._all_entries):
            assignments = self.extract_variable_assignments(entry)

            for assignment in assignments:
                # Check if variable name matches (partial match allowed)
                if search_var in assignment.variable_name or \
                   assignment.variable_name in search_var or \
                   self._fuzzy_match_variable(search_var, assignment.variable_name):

                    if include_context:
                        # Add context entries
                        start_idx = max(0, i - self.context_window)
                        end_idx = min(len(self._all_entries), i + self.context_window + 1)

                        assignment.context_before = self._all_entries[start_idx:i]
                        assignment.context_after = self._all_entries[i + 1:end_idx]

                    results.append(assignment)

                    if len(results) >= max_results:
                        return results

        # If no results, try sliding window as fallback
        if not results:
            results = self._sliding_window_search(search_var, max_results)

        return results

    def _fuzzy_match_variable(self, search_var: str, actual_var: str) -> bool:
        """Check if variable names match with fuzzy matching.

        Supports matching:
        - ${stfSeq} matches ${$.BODY_DATA.stfSeq}
        - BODY_DATA.stfSeq matches ${$.BODY_DATA.stfSeq}
        """
        # Extract key parts from variables
        search_key = search_var.replace("${", "").replace("}", "").replace("$.", "").replace(".", "")
        actual_key = actual_var.replace("${", "").replace("}", "").replace("$.", "").replace(".", "")

        return search_key.lower() in actual_key.lower() or actual_key.lower() in search_key.lower()

    def _sliding_window_search(
        self,
        variable_name: str,
        max_results: int,
    ) -> list[VariableAssignment]:
        """Search using sliding window at sub-step level.

        This is the fallback mechanism that searches through fragments
        of execution steps. The sliding window operates at the stepLog
        sub-step level granularity.

        Temporal Logic: stepChildren -> stepLog -> sub-steps
        
        Sliding Window Strategy:
        1. Group entries by (treeId, subStepIndex) for sub-step granularity
        2. Slide window across sub-steps, not just steps
        3. Include context from neighboring sub-steps within the window

        Args:
            variable_name: Variable name to search for
            max_results: Maximum number of results to return

        Returns:
            List of VariableAssignment objects found via sliding window
        """
        results = []

        # Group entries by tree_id (each tree_id represents a step)
        # Then further group by sub-step (using trace_id as sub-step indicator)
        entries_by_tree_and_trace: dict[tuple[int, int], list[LogEntry]] = {}
        for entry in self._all_entries:
            key = (entry.tree_id, entry.trace_id)
            if key not in entries_by_tree_and_trace:
                entries_by_tree_and_trace[key] = []
            entries_by_tree_and_trace[key].append(entry)

        # Get all sub-step keys in temporal order
        # Sort by tree_id first, then trace_id (which represents time order within a step)
        sub_step_keys = sorted(
            entries_by_tree_and_trace.keys(),
            key=lambda k: (k[0], k[1])
        )

        # Slide window across sub-steps
        for window_start in range(0, len(sub_step_keys), self.sliding_window_size // 2):
            window_end = min(window_start + self.sliding_window_size, len(sub_step_keys))
            window_keys = sub_step_keys[window_start:window_end]

            # Collect all entries in the window
            window_entries = []
            for key in window_keys:
                window_entries.extend(entries_by_tree_and_trace[key])

            # Search for variable in window entries
            for entry in window_entries:
                assignments = self.extract_variable_assignments(entry)

                for assignment in assignments:
                    if self._fuzzy_match_variable(variable_name, assignment.variable_name):
                        # Add context from the window
                        try:
                            pos = window_entries.index(entry)
                            assignment.context_before = window_entries[:pos][-self.context_window:]
                            assignment.context_after = window_entries[pos + 1:][:self.context_window]
                        except ValueError:
                            pass

                        # Avoid duplicates
                        if not any(r.variable_name == assignment.variable_name and 
                                   r.log_entry.create_time == assignment.log_entry.create_time
                                   for r in results):
                            results.append(assignment)

                            if len(results) >= max_results:
                                return results

        return results

    def search_by_step_name(
        self,
        step_name: str,
        max_results: int = 10,
    ) -> list[StepFragment]:
        """Search for step fragments by step name.

        Args:
            step_name: Step name to search for (partial match)
            max_results: Maximum number of fragments to return

        Returns:
            List of StepFragment objects matching the search
        """
        if not self._all_entries:
            self.load_logs()

        results = []

        # Group entries by step name
        entries_by_step: dict[str, list[LogEntry]] = {}
        for entry in self._all_entries:
            if entry.step_name not in entries_by_step:
                entries_by_step[entry.step_name] = []
            entries_by_step[entry.step_name].append(entry)

        # Search for matching steps
        for name, entries in entries_by_step.items():
            if step_name.lower() in name.lower():
                fragment = StepFragment(
                    entries=entries,
                    start_time=entries[0].create_time if entries else "",
                    end_time=entries[-1].create_time if entries else "",
                    step_name=name,
                )
                results.append(fragment)

                if len(results) >= max_results:
                    break

        return results

    def search_by_content(
        self,
        content_pattern: str,
        max_results: int = 10,
        include_context: bool = True,
    ) -> list[LogEntry]:
        """Search for log entries by content pattern.

        Args:
            content_pattern: Pattern to search for in log content
            max_results: Maximum number of entries to return
            include_context: Whether to include surrounding entries

        Returns:
            List of LogEntry objects matching the search
        """
        if not self._all_entries:
            self.load_logs()

        results = []
        pattern = re.compile(content_pattern, re.IGNORECASE)

        for i, entry in enumerate(self._all_entries):
            if pattern.search(entry.content):
                if include_context:
                    # Create a copy with context
                    entry_with_context = LogEntry(
                        trace_id=entry.trace_id,
                        content=entry.content,
                        log_level=entry.log_level,
                        create_time=entry.create_time,
                        step_name=entry.step_name,
                        tree_id=entry.tree_id,
                        parent_tree_id=entry.parent_tree_id,
                        depth=entry.depth,
                    )
                    results.append(entry_with_context)
                else:
                    results.append(entry)

                if len(results) >= max_results:
                    break

        return results

    def get_step_fragment_by_tree_id(
        self,
        tree_id: int,
        include_children: bool = True,
    ) -> Optional[StepFragment]:
        """Get a step fragment by its tree ID.

        Args:
            tree_id: Tree ID of the step
            include_children: Whether to include entries from child steps

        Returns:
            StepFragment if found, None otherwise
        """
        if not self._all_entries:
            self.load_logs()

        entries = []
        step_name = ""

        for entry in self._all_entries:
            if entry.tree_id == tree_id:
                entries.append(entry)
                if not step_name:
                    step_name = entry.step_name
            elif include_children and entry.parent_tree_id == tree_id:
                entries.append(entry)

        if not entries:
            return None

        return StepFragment(
            entries=entries,
            start_time=entries[0].create_time,
            end_time=entries[-1].create_time,
            step_name=step_name,
        )

    def get_all_variables(self) -> dict[str, list[VariableAssignment]]:
        """Extract all variable assignments from all logs.

        Returns:
            Dictionary mapping variable names to their assignments
        """
        if not self._all_entries:
            self.load_logs()

        variables: dict[str, list[VariableAssignment]] = {}

        for entry in self._all_entries:
            assignments = self.extract_variable_assignments(entry)

            for assignment in assignments:
                var_name = assignment.variable_name
                if var_name not in variables:
                    variables[var_name] = []
                variables[var_name].append(assignment)

        return variables

    def get_sub_step_fragments(
        self,
        tree_id: Optional[int] = None,
        max_results: int = 20,
    ) -> list[StepFragment]:
        """Get all sub-step fragments at stepLog sub-step level.

        This returns fragments at the finest granularity - the sub-step level
        within stepLog, grouped by (treeId, traceId).

        Args:
            tree_id: Optional tree ID to filter by. If None, returns all sub-steps.
            max_results: Maximum number of fragments to return.

        Returns:
            List of StepFragment objects, each representing a sub-step.
        """
        if not self._all_entries:
            self.load_logs()

        # Group entries by (tree_id, trace_id) - sub-step level
        entries_by_sub_step: dict[tuple[int, int], list[LogEntry]] = {}
        for entry in self._all_entries:
            if tree_id is not None and entry.tree_id != tree_id:
                continue
            key = (entry.tree_id, entry.trace_id)
            if key not in entries_by_sub_step:
                entries_by_sub_step[key] = []
            entries_by_sub_step[key].append(entry)

        # Convert to StepFragment objects
        results = []
        for (tid, sub_idx), entries in sorted(entries_by_sub_step.items()):
            if not entries:
                continue

            fragment = StepFragment(
                entries=entries,
                start_time=entries[0].create_time,
                end_time=entries[-1].create_time,
                step_name=entries[0].step_name,
                tree_id=tid,
                depth=entries[0].depth,
                sub_step_index=sub_idx,
            )
            results.append(fragment)

            if len(results) >= max_results:
                break

        return results

    def search_variable_in_sub_step(
        self,
        variable_name: str,
        tree_id: int,
        max_results: int = 5,
    ) -> list[VariableAssignment]:
        """Search for a variable within a specific step's sub-steps.

        This is useful when you know the step (treeId) and want to find
        variables within that step's stepLog sub-steps.

        Args:
            variable_name: Variable name to search for
            tree_id: Tree ID of the step to search within
            max_results: Maximum number of results to return

        Returns:
            List of VariableAssignment objects found in the step's sub-steps.
        """
        if not self._all_entries:
            self.load_logs()

        # Normalize variable name
        search_var = variable_name.strip()
        if not search_var.startswith("${"):
            search_var = "${" + search_var
        if not search_var.endswith("}"):
            search_var = search_var + "}"

        results = []

        # Filter entries by tree_id
        step_entries = [e for e in self._all_entries if e.tree_id == tree_id]

        for i, entry in enumerate(step_entries):
            assignments = self.extract_variable_assignments(entry)

            for assignment in assignments:
                if self._fuzzy_match_variable(search_var, assignment.variable_name):
                    # Add context from within the step
                    assignment.context_before = step_entries[:i]
                    assignment.context_after = step_entries[i + 1:]
                    results.append(assignment)

                    if len(results) >= max_results:
                        return results

        return results

    def get_execution_path(
        self,
        tree_id: int,
    ) -> list[StepFragment]:
        """Get the full execution path from root to a specific step.

        This traces the parent-child relationships to show how a step
        was reached in the execution flow.

        Args:
            tree_id: Tree ID of the target step

        Returns:
            List of StepFragment objects representing the execution path.
        """
        if not self._all_entries:
            self.load_logs()

        # Find the target entry
        target_entry = None
        for entry in self._all_entries:
            if entry.tree_id == tree_id:
                target_entry = entry
                break

        if not target_entry:
            return []

        # Build the path by traversing parent relationships
        path = []
        current_parent_id = target_entry.parent_tree_id

        # Get all fragments first
        all_fragments = self.get_sub_step_fragments(max_results=1000)

        # Find target fragment
        target_fragment = None
        for frag in all_fragments:
            if frag.tree_id == tree_id:
                target_fragment = frag
                break

        if target_fragment:
            path.append(target_fragment)

        # Walk up the parent chain
        visited = {tree_id}
        while current_parent_id >= 0 and current_parent_id not in visited:
            visited.add(current_parent_id)

            for frag in all_fragments:
                if frag.tree_id == current_parent_id:
                    path.insert(0, frag)
                    break

            # Find parent's parent
            for entry in self._all_entries:
                if entry.tree_id == current_parent_id:
                    current_parent_id = entry.parent_tree_id
                    break
            else:
                break

        return path


def create_log_variable_search_tool(log_searcher: Optional[LogVariableSearcher] = None):
    """Create a log_variable_search tool function.

    Args:
        log_searcher: LogVariableSearcher instance to use for searching.
                     If None, creates a new one with default settings.

    Returns:
        An async function that can be registered as a tool
    """

    async def log_variable_search(
        variable_name: str,
        search_type: str = "variable",
        max_results: int = 5,
        include_context: bool = True,
    ) -> ToolResponse:
        """Search for variable assignments in RCA log files.

        This tool searches through processed RCA logs to find variable
        assignments and related context. It supports three search types:

        1. "variable" - Search for specific variable assignments
        2. "step" - Search by step name to find execution fragments
        3. "content" - Search by content pattern in log entries

        The search maintains temporal logic (stepChildren -> stepLog -> sub-steps)
        and uses sliding window as a fallback mechanism at the sub-step level.

        Args:
            variable_name (`str`):
                The variable name, step name, or content pattern to search for.
                For variables, can use formats like "stfSeq", "${stfSeq}",
                or "${$.BODY_DATA.stfSeq}".
            search_type (`str`, optional):
                Type of search: "variable", "step", or "content".
                Defaults to "variable".
            max_results (`int`, optional):
                Maximum number of results to return. Defaults to 5.
            include_context (`bool`, optional):
                Whether to include context entries around matches.
                Defaults to True.

        Returns:
            `ToolResponse`:
                Search results with variable assignments, step fragments,
                or log entries depending on search type.
        """
        nonlocal log_searcher

        if log_searcher is None:
            # Try to find log directory
            log_dir = os.environ.get("RCA_LOGS_DIR", "rcalogs_processed")
            log_searcher = LogVariableSearcher(log_dir=log_dir)
            log_searcher.load_logs()

        try:
            if search_type == "variable":
                results = log_searcher.search_variable(
                    variable_name=variable_name,
                    max_results=max_results,
                    include_context=include_context,
                )
                output = {
                    "searchType": "variable",
                    "query": variable_name,
                    "count": len(results),
                    "results": [r.to_dict() for r in results],
                }

            elif search_type == "step":
                results = log_searcher.search_by_step_name(
                    step_name=variable_name,
                    max_results=max_results,
                )
                output = {
                    "searchType": "step",
                    "query": variable_name,
                    "count": len(results),
                    "results": [r.to_dict() for r in results],
                }

            elif search_type == "content":
                results = log_searcher.search_by_content(
                    content_pattern=variable_name,
                    max_results=max_results,
                    include_context=include_context,
                )
                output = {
                    "searchType": "content",
                    "query": variable_name,
                    "count": len(results),
                    "results": [r.to_dict() for r in results],
                }

            else:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=f"Error: Unknown search type '{search_type}'. "
                                 f"Use 'variable', 'step', or 'content'.",
                        ),
                    ],
                )

            # Format output as readable text
            text_output = _format_search_results(output)

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=text_output,
                    ),
                ],
            )

        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: Log variable search failed due to\n{e}",
                    ),
                ],
            )

    return log_variable_search


def _format_search_results(output: dict) -> str:
    """Format search results as readable text."""
    lines = []
    lines.append(f"=== Log Search Results ===")
    lines.append(f"Search Type: {output['searchType']}")
    lines.append(f"Query: {output['query']}")
    lines.append(f"Found: {output['count']} result(s)")
    lines.append("")

    for i, result in enumerate(output['results'], 1):
        lines.append(f"--- Result {i} ---")

        if output['searchType'] == "variable":
            lines.append(f"Variable: {result['variableName']}")
            lines.append(f"Value: {result['variableValue']}")

            entry = result['logEntry']
            lines.append(f"Location: Step '{entry['stepName']}' (treeId={entry['treeId']})")
            lines.append(f"Time: {entry['createTime']}")
            lines.append(f"Level: {entry['logLevel']}")

            if result.get('contextBefore'):
                lines.append(f"\nContext Before ({len(result['contextBefore'])} entries):")
                for ctx in result['contextBefore'][-2:]:
                    lines.append(f"  [{ctx['logLevel']}] {ctx['content'][:100]}...")

            if result.get('contextAfter'):
                lines.append(f"\nContext After ({len(result['contextAfter'])} entries):")
                for ctx in result['contextAfter'][:2]:
                    lines.append(f"  [{ctx['logLevel']}] {ctx['content'][:100]}...")

        elif output['searchType'] == "step":
            lines.append(f"Step Name: {result['stepName']}")
            lines.append(f"Time Range: {result['startTime']} - {result['endTime']}")
            lines.append(f"Entries: {len(result['entries'])}")

            # Show first few entries
            for entry in result['entries'][:3]:
                lines.append(f"  [{entry['logLevel']}] {entry['content'][:80]}...")

        elif output['searchType'] == "content":
            lines.append(f"Step: {result['stepName']} (treeId={result['treeId']})")
            lines.append(f"Time: {result['createTime']}")
            lines.append(f"Level: {result['logLevel']}")
            lines.append(f"Content: {result['content'][:200]}...")

        lines.append("")

    return "\n".join(lines)


# Convenience function for direct use
def search_log_variable(
    variable_name: str,
    log_dir: str = "rcalogs_processed",
    max_results: int = 5,
) -> list[VariableAssignment]:
    """Convenience function to search for a variable in logs.

    Args:
        variable_name: Variable name to search for
        log_dir: Directory containing processed log files
        max_results: Maximum number of results to return

    Returns:
        List of VariableAssignment objects
    """
    searcher = LogVariableSearcher(log_dir=log_dir)
    searcher.load_logs()
    return searcher.search_variable(variable_name, max_results=max_results)

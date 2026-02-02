"""
Process Parser Service
======================
Parses Python files for @process and @step annotations to auto-generate
process flow documentation from actual code.

Annotation Format:
-----------------
# @process: UNIQUE_PROCESS_ID
# @process_name: Human Readable Name
# @process_category: bookkeeping|operations|coordination|finance|hr
# @process_trigger: manual|scheduled|event|webhook
# @process_description: Description of what this process does
# @process_owner: Role or team responsible

# @step: 1
# @step_name: Step Name
# @step_type: condition|action|notification|wait|assignment|approval
# @step_description: What this step does
# @step_connects_to: 2,3  (optional - defaults to next step)

def some_function():
    # Implementation...
    pass
"""

import os
import re
from typing import Optional
from pathlib import Path


# Regex patterns for parsing annotations
PROCESS_PATTERN = re.compile(r'#\s*@process:\s*(.+)', re.IGNORECASE)
PROCESS_ATTR_PATTERN = re.compile(r'#\s*@process_(\w+):\s*(.+)', re.IGNORECASE)
STEP_PATTERN = re.compile(r'#\s*@step:\s*(\d+)', re.IGNORECASE)
STEP_ATTR_PATTERN = re.compile(r'#\s*@step_(\w+):\s*(.+)', re.IGNORECASE)


def parse_file(file_path: str) -> list[dict]:
    """
    Parse a single Python file for process annotations.
    Returns a list of process definitions found in the file.
    """
    processes = []
    current_process = None
    current_step = None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

    for line_num, line in enumerate(lines, 1):
        line = line.strip()

        # Check for new process definition
        process_match = PROCESS_PATTERN.match(line)
        if process_match:
            # Save previous process if exists
            if current_process:
                if current_step:
                    current_process['steps'].append(current_step)
                processes.append(current_process)

            # Start new process
            current_process = {
                'id': process_match.group(1).strip(),
                'name': process_match.group(1).strip(),  # Default, can be overridden
                'category': 'operations',
                'trigger': 'manual',
                'description': '',
                'owner': '',
                'steps': [],
                'source_file': file_path,
                'source_line': line_num,
                'is_implemented': True,  # Code-based = implemented
                'status': 'active'
            }
            current_step = None
            continue

        # Check for process attributes (must have active process)
        if current_process:
            attr_match = PROCESS_ATTR_PATTERN.match(line)
            if attr_match:
                attr_name = attr_match.group(1).lower()
                attr_value = attr_match.group(2).strip()

                if attr_name == 'name':
                    current_process['name'] = attr_value
                elif attr_name == 'category':
                    current_process['category'] = attr_value
                elif attr_name == 'trigger':
                    current_process['trigger'] = attr_value
                elif attr_name == 'description':
                    current_process['description'] = attr_value
                elif attr_name == 'owner':
                    current_process['owner'] = attr_value
                continue

            # Check for step definition
            step_match = STEP_PATTERN.match(line)
            if step_match:
                # Save previous step if exists
                if current_step:
                    current_process['steps'].append(current_step)

                # Start new step
                step_num = int(step_match.group(1))
                current_step = {
                    'number': step_num,
                    'name': f'Step {step_num}',
                    'type': 'action',
                    'description': '',
                    'connects_to': [],
                    'source_line': line_num
                }
                continue

            # Check for step attributes (must have active step)
            if current_step:
                step_attr_match = STEP_ATTR_PATTERN.match(line)
                if step_attr_match:
                    attr_name = step_attr_match.group(1).lower()
                    attr_value = step_attr_match.group(2).strip()

                    if attr_name == 'name':
                        current_step['name'] = attr_value
                    elif attr_name == 'type':
                        current_step['type'] = attr_value
                    elif attr_name == 'description':
                        current_step['description'] = attr_value
                    elif attr_name == 'connects_to':
                        # Parse comma-separated step numbers
                        try:
                            current_step['connects_to'] = [
                                int(x.strip()) for x in attr_value.split(',')
                            ]
                        except ValueError:
                            pass
                    continue

    # Don't forget the last process/step
    if current_process:
        if current_step:
            current_process['steps'].append(current_step)
        processes.append(current_process)

    # Post-process: auto-connect sequential steps if no explicit connections
    for process in processes:
        for i, step in enumerate(process['steps']):
            if not step['connects_to'] and i < len(process['steps']) - 1:
                next_step = process['steps'][i + 1]
                step['connects_to'] = [next_step['number']]

    return processes


def parse_directory(directory: str, recursive: bool = True) -> list[dict]:
    """
    Parse all Python files in a directory for process annotations.
    """
    all_processes = []

    path = Path(directory)
    pattern = '**/*.py' if recursive else '*.py'

    for py_file in path.glob(pattern):
        # Skip __pycache__ and virtual environments
        if '__pycache__' in str(py_file) or 'venv' in str(py_file):
            continue

        processes = parse_file(str(py_file))
        all_processes.extend(processes)

    return all_processes


def get_all_implemented_processes(api_root: Optional[str] = None) -> list[dict]:
    """
    Get all implemented processes from the API codebase.
    """
    if api_root is None:
        # Try to find API root relative to this file
        current_dir = Path(__file__).parent.parent.parent
        api_root = str(current_dir)

    # Parse routers and services directories
    processes = []

    routers_dir = os.path.join(api_root, 'api', 'routers')
    services_dir = os.path.join(api_root, 'api', 'services')

    if os.path.exists(routers_dir):
        processes.extend(parse_directory(routers_dir))

    if os.path.exists(services_dir):
        processes.extend(parse_directory(services_dir))

    return processes


def merge_with_database_processes(
    implemented: list[dict],
    database_drafts: list[dict]
) -> list[dict]:
    """
    Merge implemented (code-based) processes with database draft processes.
    Database drafts are processes proposed but not yet implemented.
    """
    # Create lookup by ID
    implemented_ids = {p['id'] for p in implemented}

    merged = list(implemented)

    # Add drafts that don't have implementations
    for draft in database_drafts:
        if draft['id'] not in implemented_ids:
            draft['is_implemented'] = False
            draft['status'] = 'draft'
            merged.append(draft)

    return merged


# Layout calculation for visual positioning
def calculate_layout(processes: list[dict], canvas_width: int = 2000) -> list[dict]:
    """
    Calculate visual layout positions for processes.
    Groups by category and arranges in a grid.
    """
    # Group by category
    by_category = {}
    for p in processes:
        cat = p.get('category', 'other')
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(p)

    # Category order (matching sidebar)
    category_order = ['coordination', 'bookkeeping', 'operations', 'finance', 'hr', 'other']

    # Layout parameters
    start_x = 150
    start_y = 100
    node_width = 300
    node_height = 180
    h_spacing = 80
    v_spacing = 60
    category_gap = 120

    current_y = start_y

    for cat in category_order:
        if cat not in by_category:
            continue

        cat_processes = by_category[cat]
        current_x = start_x

        for i, process in enumerate(cat_processes):
            process['position'] = {
                'x': current_x,
                'y': current_y
            }

            # Move to next column
            current_x += node_width + h_spacing

            # Wrap to next row if needed (max 3 per row)
            if (i + 1) % 3 == 0:
                current_x = start_x
                current_y += node_height + v_spacing

        # Move to next category section
        rows_used = max(1, (len(cat_processes) + 2) // 3)
        current_y += (rows_used * (node_height + v_spacing)) + category_gap

    return processes


if __name__ == '__main__':
    # Test parsing
    import json

    # Parse the API directory
    api_root = Path(__file__).parent.parent.parent
    processes = get_all_implemented_processes(str(api_root))

    print(f"Found {len(processes)} processes:")
    print(json.dumps(processes, indent=2, default=str))

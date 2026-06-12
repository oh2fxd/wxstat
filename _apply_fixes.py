#!/usr/bin/env python3
"""Apply the remaining JS fixes to wx_server.py"""
import os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_server.py")
with open(path, "r") as f:
    content = f.read()

# --- Find the refresh function text ---
# The issue: tabs vs spaces in the inline string. Let's detect the pattern.
# Search for the key unique strings and find their exact form
searches = [
    ("if (!r.ok)", "return;"),
    ("try {", 'hr = await fetch'),
    ("     drawChart", ""),  # After history fetch
]

# Find these substrings and report their exact bytes
for needle, context in searches:
    idx = content.find(needle)
    if idx >= 0:
        end = idx + 80
        snippet = content[idx:end]
        print(f"Found '{needle}' at {idx}: {repr(snippet)}")
    else:
        print(f"NOT FOUND: {needle}")

# Find the exact "if (!r.ok) return;" pattern
idx = content.find("if (!r.ok) return;")
if idx >= 0:
    # Get 10 chars before to see indentation
    prefix = content[max(0,idx-10):idx]
    suffix = content[idx:idx+80]
    print(f"\nPrefix: {repr(prefix)}")
    print(f"Suffix: {repr(suffix)}")

    # Now apply the edit - replace this exact string
    old = content[idx:idx + len("if (!r.ok) return;")]
    print(f"Old string: {repr(old)}")
else:
    print("Could not find 'if (!r.ok) return;' in file")

# Find the history fetch section
idx2 = content.find("'/api/history?limit=288'")
if idx2 >= 0:
    print(f"\nHistory fetch at {idx2}: {repr(content[idx2:idx2+100])}")
else:
    print("Could not find history fetch URL")

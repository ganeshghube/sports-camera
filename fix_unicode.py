#!/usr/bin/env python3
"""Fix Unicode encoding issue in 3_build_dataset.py"""
import sys

filepath = 'C:/00_Ganesh/sports-camera/tools/train/3_build_dataset.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the emoji checkmark with ASCII text to avoid cp1252 encoding issues
old_line = 'print(f"\\n✅ Dataset ready at: {out_dir}/")'
new_line = 'print(f"\\nSUCCESS Dataset ready at: {out_dir}/")'

if old_line in content:
    content = content.replace(old_line, new_line)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print('File updated successfully: replaced ✅ with SUCCESS')
else:
    print('Old line not found, checking for Dataset ready line...')
    if 'Dataset ready' in content:
        # Try a more flexible replacement
        import re
        # Replace the specific print pattern
        content = content.replace('✅', 'SUCCESS')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print('File updated successfully: replaced ✅ with SUCCESS')
    else:
        print('No Dataset ready line found')
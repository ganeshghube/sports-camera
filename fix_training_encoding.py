#!/usr/bin/env python3
"""Fix encoding issues in training_router.py"""
import re

filepath = 'C:/00_Ganesh/sports-camera/backend/training_router.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Add encoding='utf-8' to first subprocess.run (dataset build)
# Find capture_output=True, text=True and add encoding='utf-8'
content = content.replace(
    "capture_output=True, text=True, cwd=str(ROOT)",
    "capture_output=True, text=True, encoding='utf-8', cwd=str(ROOT)"
)
# Do it twice since there are two subprocess.run calls
content = content.replace(
    "capture_output=True, text=True, encoding='utf-8', cwd=str(ROOT)",
    "capture_output=True, text=True, encoding='utf-8', cwd=str(ROOT)"
, 1)  # Only replace first occurrence

# Actually, let me be more precise - replace only the first occurrence
first_marker = "capture_output=True, text=True, cwd=str(ROOT)"
content = content.replace(first_marker, "capture_output=True, text=True, encoding='utf-8', cwd=str(ROOT)", 1)

# Fix 2: Replace ✅ with SUCCESS in log messages
content = content.replace("_'✅ Training complete!'_", "_'SUCCESS Training complete!'_")
content = content.replace("_'❌ Dataset build failed'_,", "_'ERROR Dataset build failed'_,")

# Actually the quotes are different - let me check the exact pattern
# The actual patterns in the file are:
# _log("❌ Dataset build failed")
# _log("✅ Training complete!")

# Let me just replace the emoji checkmarks with ASCII text
content = content.replace('_"✅ Training complete!"_', '_"SUCCESS Training complete!"_')
content = content.replace('_"❌ Dataset build failed"_,', '_"ERROR Dataset build failed"_,')
content = content.replace('_"❌ Training process failed — check log above"_,', '_"ERROR Training process failed — check log above"_,')

# Fix 3: The _log function already has the ASCII encoding fix from earlier

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
print('Training router encoding fixes applied')
import sys, os
# Ensure the app root is always on the path so 'config' is importable
root = os.path.dirname(os.path.abspath(__file__))
if root not in sys.path:
    sys.path.insert(0, root)

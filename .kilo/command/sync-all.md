---
name: sync-all
description: Run all sync operations (deps, db, commands)
category: deployment
---

```bash
# Run all sync operations in order
echo "=== Starting full sync ==="

# Sync dependencies
echo ""
echo "[1/3] Syncing dependencies..."
pip install -r requirements.txt

# Sync database
echo ""
echo "[2/3] Database sync:"
echo "  Please execute schema.sql in Supabase SQL editor manually."
echo "  Or run: supabase db push"
echo "  Skipping automatic DB sync to prevent data loss."

# Sync Discord commands
echo ""
echo "[3/3] Syncing Discord slash commands..."
python deploy.py

echo ""
echo "=== Sync complete ==="
```

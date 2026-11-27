"""Read-only cloud check: python -m app.db_check. No credentials in output."""
import json
import sys
from app.database import database_health, pool

if __name__ == '__main__':
    result = database_health()
    print(json.dumps(result, ensure_ascii=False))
    if pool.cache_info().currsize:
        pool().close()
    sys.exit(0 if result['status'] == 'ok' else 1)

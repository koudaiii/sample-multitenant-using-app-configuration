"""Read run metadata as data, never as executable shell code."""

import json
from pathlib import Path
import re
import sys


def write(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


action, filename, *args = sys.argv[1:]
path = Path(filename)
if action == 'create':
    run_id, sample, mode, subscription, location, principal = args
    write(path, dict(run_id=run_id, sample=sample, mode=mode,
                     subscription=subscription, location=location, principal=principal,
                     resource_group=f'rg-mtappconfig-{sample}-{run_id}', status='preparing'))
else:
    data = json.loads(path.read_text())
    if action == 'get':
        print(data[args[0]])
    elif action == 'set':
        data[args[0]] = args[1]
        write(path, data)
    elif action == 'validate':
        run_id = args[0]
        if not (data['run_id'] == run_id
                and data['sample'] in ('01', '02', '03', '04')
                and data['mode'] in ('local', 'azure')
                and data['resource_group'] == f"rg-mtappconfig-{data['sample']}-{run_id}"
                and (data['mode'] == 'local' or
                     re.fullmatch(r'[a-fA-F0-9-]{36}', data['subscription']))):
            sys.exit('Run metadata is invalid; refusing to use it.')
    else:
        sys.exit('Unknown state operation')

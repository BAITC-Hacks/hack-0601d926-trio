"""Rebuild runtime lock from the installed, tested environment."""
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement

pending = ['fastapi', 'uvicorn', 'pandas', 'pyarrow', 'networkx', 'numpy', 'scipy']
seen = {}
while pending:
    package = distribution(pending.pop())
    name = package.metadata['Name']
    if name in seen:
        continue
    seen[name] = package.version
    for raw in package.requires or []:
        requirement = Requirement(raw)
        if requirement.marker is None or requirement.marker.evaluate():
            pending.append(requirement.name)
target = Path(__file__).resolve().parents[1] / 'backend' / 'requirements.txt'
target.write_text(
    '# Runtime lock generated from the verified Python 3.12 environment.\n'
    + '\n'.join(f'{name}=={version}' for name, version in sorted(seen.items(), key=lambda x: x[0].lower()))
    + '\n', encoding='utf-8',
)
print(target)

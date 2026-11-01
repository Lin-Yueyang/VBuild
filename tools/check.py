"""Read installed dependency metadata; never install or download packages."""

import importlib.metadata as metadata
import re
import sys

from packaging.requirements import Requirement


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


installed = {normalize(distribution.metadata["Name"]): distribution
             for distribution in metadata.distributions()}
roots = ["numpy", "ase", "scipy", "PySide6", "pyvista", "pyvistaqt", "spglib", "matplotlib"]
pending = roots.copy()
seen = set()
missing = set()
conflicts = set()

while pending:
    name = normalize(pending.pop())
    if name in seen:
        continue
    seen.add(name)
    if name not in installed:
        missing.add(name)
        continue
    for text in installed[name].requires or []:
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        dependency = normalize(requirement.name)
        if (dependency in installed and requirement.specifier
                and not requirement.specifier.contains(installed[dependency].version)):
            conflicts.add(f"{requirement}: installed {installed[dependency].version}")
        pending.append(dependency)

print("Python:", sys.version.split()[0])
for name in roots:
    distribution = installed.get(normalize(name))
    print(f"{name}: {distribution.version if distribution else 'MISSING'}")
print("Missing dependencies:", ", ".join(sorted(missing)) or "none")
print("Version conflicts:", "; ".join(sorted(conflicts)) or "none")
sys.exit(int(bool(missing or conflicts)))

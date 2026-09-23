"""Normalize this plan's six public-source citation records; no library writes."""
from pathlib import Path
import re

root = Path(__file__).resolve().parent
notes = {
    '2505.23752': 'Agent evaluation. Verified arXiv v3: 486 tasks and 1778 expert-verified steps; not 436 tasks in older local notes.',
    '2509.23141': 'Agent/tool architecture and evaluation. Export records the arXiv version; official Earth-Agent repository reports ICLR 2026 acceptance.',
    '2407.11743': 'External tree-cover dataset candidate; does not replace local UAV field validation.',
    '2512.15231': 'Preprint on procedural knowledge and replanning. Later-stage research reference, not a required dependency.',
    '10.1002/rse2.332': 'Published tree-crown delineation method associated with detectree2. Validate on the target forest before adoption.',
    '10.1007/s10980-025-02193-y': 'Published UAS fractional vegetation-cover study. Grassland/rangeland workflow reference; transfer must be validated locally.',
}
records = []
for path in sorted((root / 'references').glob('*.ris')):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    clean = []
    identifier = None
    for line in lines:
        if not line.strip() or line.startswith(('N2  -', 'AB  -', 'ER  -', 'N1  -', 'KW  -')):
            continue
        if line.startswith('DO  -'):
            identifier = line[6:].strip()
            if re.fullmatch(r'\d{4}\.\d{4,5}(v\d+)?', identifier):
                identifier = re.sub(r'v\d+$', '', identifier)
                line = 'DO  - 10.48550/arXiv.' + identifier
            elif identifier.startswith('10.48550/arXiv.'):
                identifier = identifier.removeprefix('10.48550/arXiv.')
        clean.append(line)
    assert identifier in notes, identifier
    clean += ['KW  - Forestry Agent Development', 'N1  - ' + notes[identifier], 'ER  -', '']
    record = '\n'.join(clean) + '\n'
    path.write_text(record, encoding='utf-8')
    records.append(record)
assert len(records) == 6
target = root / 'development_references.ris'
target.write_text(''.join(records), encoding='utf-8')
print('Prepared six verified citation records:', target.name)

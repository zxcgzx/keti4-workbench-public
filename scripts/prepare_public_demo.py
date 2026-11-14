"""Create a fully synthetic fixture without reading any private source."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bus = 'BUS_I BUS_TYPE PD QD GS BS BUS_AREA VM VA BASE_KV ZONE VMAX VMIN RENEWABLE'
gen = 'GEN_BUS PG QG QMAX QMIN VG MBASE GEN_STATUS PMAX PMIN'
branch = 'F_BUS T_BUS BR_R BR_X BR_B RATE_A RATE_B RATE_C TAP SHIFT BR_STATUS ANGMIN ANGMAX PF QF PT QT'
parts = ['#####Begin Dict Info#####', 'time=2026-01-01T00:00:00', 'baseMVA=100', 'source=SYNTHETIC_PUBLIC_DEMO_NOT_REAL_GRID', '#####End Dict Info#####']
for name, columns, rows in [
    ('Bus', bus, [[i, 3 if i == 1 else 1, 2, .5, 0, 0, 1, 1, 0, 220, 1, 1.1, .9, 0] for i in range(1, 61)]),
    ('Gen', gen, [[i, 10, 2.5, 50, -50, 1, 100, 1, 100, 0] for i in range(1, 61, 5)]),
    ('Branch', branch, [[i, (i-1+offset)%60+1, .01, .1, 0, 100, 100, 100, 0, 0, 1, -360, 360, 1, 0, -1, 0] for offset in [1, 7, 13] for i in range(1, 61)]),
]:
    parts += [f'#####Begin Matrix {name}#####', ','.join(columns.split())]
    parts += [','.join(map(str, row)) for row in rows]
    parts += [f'#####End Matrix {name}#####']
path = ROOT / 'data/public-demo/synthetic_public.txt'
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text('\n'.join(parts) + '\n', encoding='utf-8')
print('Created synthetic public fixture:', path.name)

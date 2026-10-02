from pathlib import Path

source = Path(__file__).with_name('reproduce_io_capacity.py').read_text(encoding='utf-8')
source = source.replace('capacity-before-20261001', 'capacity-before-r2-20261001').replace('io-capacity-before.json', 'io-capacity-before-r2.json')
exec(compile(source, str(__file__), 'exec'))

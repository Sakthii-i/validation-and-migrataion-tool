import pathlib
p = pathlib.Path(r'c:\Users\mridu\Downloads\validation_tool_new\validation_tool\api\migration_routes.py')
lines = p.read_text(encoding='utf-8').splitlines(keepends=True)
# Remove lines 142-150 (0-indexed: 141-149) — duplicate leftover
new_lines = lines[:141] + lines[150:]
p.write_text(''.join(new_lines), encoding='utf-8')
print(f'Removed lines 142-150, total lines: {len(lines)} -> {len(new_lines)}')

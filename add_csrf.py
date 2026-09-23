import glob
import re
for filepath in glob.glob('templates/*.html'):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if '<form' in content and 'csrf_token' not in content:
        content = re.sub(r'(<form[^>]*>)', r'\1\n      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">', content)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'CSRF added to {filepath}')

import sys
html = open('templates/layout.html', 'r', encoding='utf-8').read()
html = html.replace('<aside class="sidebar">', '{% if session.get(''user_id'') %}<aside class="sidebar">')
html = html.replace('</aside>', '</aside>{% endif %}')
html = html.replace('<a class="button primary" href="{{ url_for(''anuncios'') }}">+ Criar anúncio</a></div>', '<a class="button primary" href="{{ url_for(''anuncios'') }}">+ Criar anúncio</a><form method="post" action="{{ url_for(''logout'') }}" style="display:inline;"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button class="button secondary" style="margin-left:8px;">Sair</button></form></div>')
html = html.replace('<div class="top-actions">', '{% if session.get(''user_id'') %}<div class="top-actions">')
html = html.replace('</form></div>', '</form></div>{% endif %}')
open('templates/layout.html', 'w', encoding='utf-8').write(html)

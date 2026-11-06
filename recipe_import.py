"""Shared lossless JSON-LD importer. No application import or implicit DB writes."""
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import unicodedata
from fractions import Fraction
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, urljoin, parse_qsl, urlencode

MAX_BYTES = 3_000_000


def canonical_url(value):
    if isinstance(value, dict):
        value = value.get('@id') or value.get('url')
    if not isinstance(value, str) or not value.strip():
        return ''
    value = value.strip()
    if value.startswith('//'):
        value = 'https:' + value
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Source URL must be public HTTP(S), without credentials')
    query = '' if p.hostname.lower() == 'cooking.nytimes.com' else urlencode(sorted((k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')))
    scheme = 'https' if p.hostname.lower() == 'cooking.nytimes.com' else p.scheme.lower()
    return urlunsplit((scheme, p.netloc.lower(), p.path.rstrip('/') or '/', query, ''))


def public_addresses(url):
    canonical = canonical_url(url)
    if not canonical:
        raise ValueError('A public HTTP(S) URL is required')
    p = urlsplit(canonical)
    if p.port not in (None, 80, 443):
        raise ValueError('Only standard HTTP(S) ports are allowed')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Blocked non-public network target')
    return p, addresses


def fetch_url(url):
    """Resolve every hop; pin connection to vetted IP, retaining TLS hostname checks."""
    deadline = time.monotonic() + 20
    for _ in range(6):
        p, addresses = public_addresses(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Import fetch timed out')
        port = p.port or (443 if p.scheme == 'https' else 80)
        conn = http.client.HTTPConnection(p.hostname, port, timeout=min(remaining, 8))
        try:
            family, socktype, proto, _, addr = addresses[0]
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(min(remaining, 8))
            conn.sock = sock
            sock.connect(addr)
            if p.scheme == 'https':
                conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=p.hostname)
            sock = conn.sock
            target = urlunsplit(('', '', p.path or '/', p.query, ''))
            conn.request('GET', target, headers={'Host': p.netloc, 'User-Agent': 'PantryPlanner/1.0', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location') or '')
                continue
            if response.status != 200:
                raise ValueError(f'Source returned HTTP {response.status}; paste permitted JSON-LD/HTML instead (no paywall bypass)')
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError('Import fetch timed out')
                sock.settimeout(min(8, max(.01, deadline - time.monotonic())))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError('Source exceeds 3 MB limit')
            # Some CDNs gzip regardless of Accept-Encoding; detect by header or magic bytes.
            encoding = (response.getheader('Content-Encoding') or '').lower()
            if encoding == 'gzip' or data[:2] == b'\x1f\x8b':
                import gzip
                data = gzip.decompress(bytes(data))
            elif encoding == 'deflate' or data[:2] == b'\x78\x9c':
                import zlib
                data = zlib.decompress(bytes(data))
            return data.decode('utf-8', errors='replace'), url
        finally:
            conn.close()
    raise ValueError('Too many redirects')


class LDHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks, self.parts, self.tag = [], [], None
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'div') and dict(attrs).get('type', '').lower() == 'application/ld+json':
            self.tag, self.parts = tag, []
    def handle_data(self, data):
        if self.tag:
            self.parts.append(data)
    def handle_entityref(self, name):
        self.handle_data('&' + name + ';')
    def handle_charref(self, name):
        self.handle_data('&#' + name + ';')
    def handle_endtag(self, tag):
        if tag == self.tag:
            self.blocks.append(''.join(self.parts)); self.tag = None


def find_recipe(value):
    if isinstance(value, list):
        for item in value:
            found = find_recipe(item)
            if found:
                return found
    if isinstance(value, dict):
        if 'Recipe' in as_list(value.get('@type')):
            return value
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = find_recipe(item)
                if found:
                    return found
    return None


def extract_recipe(content):
    if isinstance(content, (dict, list)):
        found = find_recipe(content)
    else:
        if len(content.encode()) > MAX_BYTES:
            raise ValueError('Import exceeds 3 MB limit')
        try:
            found = find_recipe(json.loads(content))
        except (ValueError, TypeError):
            parser = LDHTML(); parser.feed(content)
            found = None
            for block in parser.blocks:
                for candidate in (block, html.unescape(block)):
                    try:
                        found = find_recipe(json.loads(candidate))
                    except ValueError:
                        continue
                    if found:
                        break
                if found:
                    break
    if not found:
        raise ValueError('No Recipe JSON-LD found. Source may be blocked; paste permitted JSON-LD or HTML.')
    return found


def as_list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def text(value):
    return html.unescape(re.sub('<[^>]*>', '', str(value or ''))).strip()


def tags(value):
    return list(dict.fromkeys(text(v).strip() for x in as_list(value) for v in str(x).split(',') if text(v).strip()))


def number(value):
    try:
        n = float(str(value).replace(',', ''))
        return n if n == n and abs(n) != float('inf') else None
    except (ValueError, TypeError):
        return None


NUT_ALTERNATE = re.compile(
    r'\b(cashews?|almonds?|walnuts?|pistachios?|pecans?|macadamia(?:s)?(?:\s+nut)?|'
    r'hazelnuts?|brazil\s+nuts?|sunflower\s+seeds?|pumpkin\s+seeds?|pine\s+nuts?)\b', re.I)
PEANUT_WORD = re.compile(r'\b(peanuts?|groundnuts?|arachis)\b', re.I)
OIL_WORD = re.compile(r'\boils?\b', re.I)
# Singular "peanut"/"groundnut" used adjectivally on an oil line.
# Blocked when it clearly modifies a real peanut product (butter/sauce/paste/...).
PEANUT_AS_OIL = re.compile(
    r'\b(?:peanut|groundnut)\b(?![\s-]*(?:butter|sauce|paste|meal|flour|salt|skins?|shells?))', re.I)


def ingredient(raw):
    raw = text(raw)
    note = ""
    if PEANUT_WORD.search(raw):
        # "peanut or safflower oil", "peanut, canola or grape seed oil",
        # "neutral oil, like peanut or grapeseed": peanut names an oil option.
        without_oil = PEANUT_AS_OIL.sub("", raw) if OIL_WORD.search(raw) else raw
        if not PEANUT_WORD.search(without_oil):
            note = "peanut oil (refined — tolerated in this household)"
        elif NUT_ALTERNATE.search(without_oil) and re.search(r'\bor\b', without_oil, re.I):
            # Alternation like "peanuts or cashews": pick the non-peanut option.
            note = "peanut listed as one option — choose the non-peanut alternate"
        else:
            raise ValueError('Peanut ingredient rejected: ' + raw)
    s = ''.join(' ' + str(Fraction(unicodedata.numeric(c)).limit_denominator(100)).strip() if c in '¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞' else c for c in raw)
    s = s.replace('⁄', '/')
    m = re.match(r'^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)(?=\s|\(|$)', s)
    qty, rest = 0, raw
    if m:
        try:
            qty = sum(float(Fraction(v)) for v in m[1].split())
            rest = s[m.end():].strip()
        except (ValueError, ZeroDivisionError):
            pass
    # Ambiguous ranges are retained intact, not silently over/under-counted.
    if re.match(r'^\d.*?(?:–|—|-|\bto\b)\s*\d', s):
        qty, rest = 0, raw
        # strip the leading numeric range ("1 1/2 to 2 ", "12 to 16 ") so the
        # name stays an inventory identity; qty conservatively stays 0
        rest = re.sub(r'^\d+(?:\.\d+)?(?:\s+\d+/\d+|\s*(?:[–—-]|to|/)\s*\d+(?:\.\d+)?)*\s*(?:of\s+)?', '', rest)
    package = re.match(r'^\([^)]*\)\s*', rest)
    if package:
        rest = rest[package.end():]
    unitmatch = re.match(r'^(cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|lbs?|ounces?|oz|grams?|g|kg|ml|liters?|cans?|cloves?|packages?|bunches?|slices?|sticks?)\b\.?\s*(?:of\s+)?', rest, re.I)
    unit = unitmatch[1].lower() if unitmatch else ''
    name = rest[unitmatch.end():] if unitmatch else rest
    name = name.strip()
    # Cheap regex surgery: inventory identity is the head noun phrase; a trailing
    # prep/purpose clause ("divided", "for the sauce", "thinly sliced") is kept as
    # a note, never part of the name. Commas/semicolons split ONLY when the next
    # clause starts with a prep/purpose cue word, so "boneless, skinless chicken"
    # and "salt and pepper" stay intact; an opening paren always splits.
    prep = ""
    cues = (r'divided|plus|for\b|to taste|to serve|optional|drained|rinsed|pat(?:ted)?\b|'
            r'chopped|sliced|diced|minced|trimmed|halved|quartered|peeled|cored|seeded|stemmed|'
            r'zested|juiced|grated|shredded|cubed|cut\b|softened|melted|room temperature|at room|'
            r'cold\b|warm\b|hot\b|low-sodium|reduced-sodium|no-salt|unsalted|sweetened|reserve|'
            r'loosely|lightly|finely|coarsely|as needed|such as|like\b|preferably|only\b|or\b')
    m = re.search(r'\(|[,;]\s*(?:' + cues + r')', name, re.I)
    if m and m.start() > 0 and name[:m.start()].strip():
        name, prep = name[:m.start()].strip(), name[m.start():].lstrip(',;( ').strip().rstrip(').;')
    out = {'raw': raw, 'qty': qty, 'unit': unit, 'name': name or raw}
    if prep:
        out['prep'] = prep
    if note:
        out['allergy_note'] = note
    return out


def instructions(value):
    out = []
    for v in as_list(value):
        if isinstance(v, str):
            out.append({'type': 'step', 'text': text(v)})
        elif isinstance(v, dict):
            if v.get('itemListElement') is not None:
                out.append({'type': 'section', 'name': text(v.get('name')), 'steps': instructions(v['itemListElement'])})
            else:
                out.append({'type': 'step', 'name': text(v.get('name')), 'text': text(v.get('text') or v.get('description')), 'url': v.get('url', '')})
    return out


def flatten_steps(nodes):
    return '\n'.join(n.get('text', '') if n['type'] == 'step' else n['name'] + '\n' + flatten_steps(n['steps']) for n in nodes)


def minutes(value):
    m = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', str(value or ''))
    return round(sum(float(x or 0) * factor for x, factor in zip(m.groups(), (1440, 60, 1, 1/60)))) if m else 0


def normalize(raw, source_url=''):
    url = canonical_url(raw.get('url') or raw.get('mainEntityOfPage') or source_url)
    name = text(raw.get('name') or raw.get('headline'))
    if not name:
        raise ValueError('Recipe has no name')
    ingredients, sections = [], []
    for item in as_list(raw.get('recipeIngredient')):
        if isinstance(item, dict):
            section = {'name': text(item.get('name')), 'ingredients': [ingredient(v) for v in as_list(item.get('itemListElement'))]}
            sections.append(section); ingredients.extend(section['ingredients'])
        else:
            parsed = ingredient(item)
            if parsed['raw'].endswith(':'):
                sections.append({'name': parsed['raw'], 'ingredients': []})
            else:
                ingredients.append(parsed)
                if sections:
                    sections[-1]['ingredients'].append(parsed)
    if sections:
        included = {id(i) for section in sections for i in section['ingredients']}
        unsectioned = [i for i in ingredients if id(i) not in included]
        if unsectioned:
            sections.insert(0, {'name': 'Ingredients', 'ingredients': unsectioned})
    nodes = instructions(raw.get('recipeInstructions'))
    rating = raw.get('aggregateRating') or {}
    if not isinstance(rating, dict):
        rating = {}
    rating = {k: number(rating.get(key)) for k, key in [('value','ratingValue'),('count','ratingCount'),('review_count','reviewCount'),('best','bestRating'),('worst','worstRating')]}
    if rating['value'] is not None:
        rating['best'] = rating['best'] if rating['best'] is not None else 5
        rating['worst'] = rating['worst'] if rating['worst'] is not None else 1
    nutrition = raw.get('nutrition') or {}
    protein = re.search(r'[\d.]+', str(nutrition.get('proteinContent', ''))) if isinstance(nutrition, dict) else None
    alltags = list(dict.fromkeys(t for key in ('keywords','recipeCategory','recipeCuisine','suitableForDiet','cookingMethod') for t in tags(raw.get(key))))
    total = minutes(raw.get('totalTime')) or minutes(raw.get('prepTime')) + minutes(raw.get('cookTime'))
    yield_text = text(raw.get('recipeYield'))
    servings = re.search(r'\d+', yield_text)
    publisher = raw.get('publisher') or {}
    source_name = 'NYT Cooking' if urlsplit(url).hostname == 'cooking.nytimes.com' else text(publisher.get('name') if isinstance(publisher, dict) else publisher) or (urlsplit(url).hostname or '')
    metadata = {'raw_jsonld': raw, 'source_name': source_name, 'source_id': raw.get('@id') or raw.get('identifier'), 'description': text(raw.get('description')), 'authors': as_list(raw.get('author')), 'images': as_list(raw.get('image')), 'rating': rating, 'ingredient_sections': sections, 'instructions': nodes, 'yield_text': yield_text, 'times': {k: raw.get(k) for k in ('prepTime','cookTime','totalTime')}, 'nutrition': nutrition, 'nutrition_provenance': {'source_url': url, 'basis': 'source supplied; not independently verified', 'protein_field': 'nutrition.proteinContent'}, 'reviews': as_list(raw.get('review')), 'video': as_list(raw.get('video')), 'tips': raw.get('tips') or raw.get('recipeTips'), 'dates': {k: raw.get(k) for k in ('datePublished','dateModified')}, 'classifications': {k: tags(raw.get(k)) for k in ('recipeCuisine','recipeCategory','keywords','suitableForDiet','cookingMethod')}}
    return {'name': name, 'source_url': url, 'metadata': metadata, 'ingredients': ingredients, 'steps': flatten_steps(nodes), 'tags': alltags, 'minutes': total, 'kind': 'weeknight' if total and total <= 45 else 'weekend', 'servings': int(servings[0]) if servings else 4, 'protein_per_serving': float(protein[0]) if protein else 0}


def migrate(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(recipes)')}
    for col, spec in [('source_url', "TEXT DEFAULT ''"), ('metadata', "TEXT DEFAULT '{}'")]:
        if col not in columns:
            conn.execute(f'ALTER TABLE recipes ADD COLUMN {col} {spec}')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS recipes_source_url ON recipes(source_url) WHERE source_url <> ''")


def upsert(conn, recipe):
    r = dict(recipe)
    existing = conn.execute('SELECT id FROM recipes WHERE source_url=?', (r['source_url'],)).fetchone() if r['source_url'] else None
    if not existing:
        base, suffix = r['name'], 1
        while conn.execute('SELECT id FROM recipes WHERE name=?', (r['name'],)).fetchone():
            suffix += 1
            r['name'] = f'{base} [{r["metadata"]["source_name"] or "import"} {suffix}]'
    else:
        # Preserve locally assigned display name, avoiding unique-name collisions.
        r['name'] = conn.execute('SELECT name FROM recipes WHERE id=?', (existing[0],)).fetchone()[0]
    columns = ['name','kind','minutes','servings','protein_per_serving','ingredients','steps','tags','source_url','metadata']
    values = [json.dumps(r[k], ensure_ascii=False) if k in ('ingredients','metadata') else ','.join(r[k]) if k == 'tags' else r[k] for k in columns]
    if existing:
        conn.execute('UPDATE recipes SET ' + ','.join(k+'=?' for k in columns) + ' WHERE id=?', values + [existing[0]])
        return {'id': existing[0], 'updated': True}
    cur = conn.execute('INSERT INTO recipes(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')', values)
    return {'id': cur.lastrowid, 'updated': False}

"""Refresh aggregate profile visuals from GitHub's public contribution calendars.

Only public calendar counts are read. No private repository API or personal token
is used. Missing or inconsistent source data fails before any files are replaced.
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import time
from datetime import date, datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

USER = 'seum14'
FIRST_YEAR = 2022
COLORS = ['#192b41', '#245b64', '#288f8b', '#36bfa5', '#68e5bd']


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self.tips, self.headings = [], {}, []
        self.capture = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'td' and 'data-date' in a:
            self.cells.append({'date': a['data-date'], 'level': int(a['data-level']), 'label': a.get('aria-labelledby'), 'id': a.get('id')})
        if tag == 'tool-tip':
            self.capture, self.text = ('tip', a.get('id'), a.get('for')), []
        elif tag == 'h2':
            self.capture, self.text = ('heading',), []

    def handle_data(self, value):
        if self.capture:
            self.text.append(value)

    def handle_endtag(self, tag):
        if not self.capture:
            return
        if tag == 'tool-tip' and self.capture[0] == 'tip':
            text = ' '.join(''.join(self.text).split())
            for key in self.capture[1:]:
                if key:
                    self.tips[key] = text
            self.capture = None
        elif tag == 'h2' and self.capture[0] == 'heading':
            self.headings.append(' '.join(''.join(self.text).split()))
            self.capture = None

    def result(self, url):
        heading = next((h for h in self.headings if re.match(r'^[\d,]+ contributions? (?:in|over) ', h)), None)
        if not heading or not self.cells:
            raise ValueError(f'Contribution calendar missing: {url}')
        days = []
        for cell in self.cells:
            text = self.tips.get(cell['label']) or self.tips.get(cell['id'], '')
            match = re.match(r'^([\d,]+) contributions?\b', text)
            if text.startswith('No contributions'):
                count = 0
            elif match:
                count = int(match[1].replace(',', ''))
            else:
                raise ValueError(f'Contribution count unavailable for {cell["date"]}')
            days.append({'date': cell['date'], 'count': count, 'level': cell['level']})
        return {'heading': heading, 'url': url, 'days': days}


def fetch_calendar(year=None):
    url = f'https://github.com/users/{USER}/contributions'
    if year is not None:
        url += f'?from={year}-01-01&to={year}-12-31'
    for attempt in range(3):
        try:
            req = Request(url, headers={'User-Agent': 'seum14-profile-contribution-refresh', 'Accept-Language': 'en-US'})
            with urlopen(req, timeout=30) as response:
                html = response.read().decode('utf-8')
            parser = CalendarParser()
            parser.feed(html)
            return parser.result(url)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def heading_total(dataset):
    return int(re.match(r'^([\d,]+)', dataset['heading']).group(1).replace(',', ''))


def summarize(data, today):
    annual = []
    for entry in sorted(data['years'], key=lambda y: y['year']):
        year = entry['year']
        days = sorted((d for d in entry['days'] if d['date'].startswith(str(year)) and d['date'] <= today.isoformat()), key=lambda d: d['date'])
        total = sum(d['count'] for d in days)
        if total != heading_total(entry):
            raise ValueError(f'Calendar total does not match its daily counts for {year}')
        annual.append({'year': year, 'contributions': total, 'source': entry['url']})
    if [y['year'] for y in annual] != list(range(FIRST_YEAR, today.year + 1)):
        raise ValueError('Lifetime contribution history is incomplete')
    current = next(e for e in data['years'] if e['year'] == today.year)
    days = sorted((d for d in current['days'] if d['date'].startswith(str(today.year)) and d['date'] <= today.isoformat()), key=lambda d: d['date'])
    streak = best = 0
    for day in days:
        streak = streak + 1 if day['count'] else 0
        best = max(best, streak)
    recent = data['recent']
    if sum(d['count'] for d in recent['days']) != heading_total(recent):
        raise ValueError('Past-year calendar total does not match its daily counts')
    return {
        'user': USER, 'updated_at': data['collectedAt'], 'first_year': FIRST_YEAR,
        'through_date': today.isoformat(), 'lifetime_contributions': sum(y['contributions'] for y in annual),
        'current_year': today.year, 'current_year_contributions': heading_total(current),
        'active_days_this_year': sum(d['count'] > 0 for d in days), 'longest_streak_this_year': best,
        'past_year_contributions': heading_total(recent), 'annual': annual,
        'scope': 'Public and anonymized private contributions shown on the public GitHub profile',
        'source': f'https://github.com/{USER}',
    }


def text(x, y, value, size=16, color='#c1d1e4', weight=400, extra=''):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" {extra}>{escape(str(value))}</text>'


def svg(width, height, body, title):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title"><title id="title">{escape(title)}</title><defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#0c1527"/><stop offset="1" stop-color="#111e35"/></linearGradient></defs><style>text{{font-family:Segoe UI,Arial,sans-serif}}.signal{{animation:pulse 4s ease-in-out infinite}}@keyframes pulse{{0%,100%{{opacity:.4}}50%{{opacity:1}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important}}}}</style><rect width="{width}" height="{height}" rx="19" fill="url(#bg)"/><rect x="1" y="1" width="{width-2}" height="{height-2}" rx="18" fill="none" stroke="#2d405d"/>{body}</svg>'''


def stats(summary, mobile=False):
    year = summary['current_year']
    metrics = [
        ('TOTAL CONTRIBUTIONS', summary['lifetime_contributions'], f'Since {FIRST_YEAR}', '#65e5bf'),
        (f'CONTRIBUTIONS IN {year}', summary['current_year_contributions'], 'Year to date', '#7fc6ff'),
        (f'ACTIVE DAYS IN {year}', summary['active_days_this_year'], 'Days with a contribution', '#b49bff'),
        (f'LONGEST STREAK IN {year}', summary['longest_streak_this_year'], 'Consecutive active days', '#ffce85'),
    ]
    width, height = (600, 510) if mobile else (1200, 260)
    body = text(28, 38, 'CONTRIBUTION RECORD', 17 if mobile else 14, '#9bb2cd', 600, 'letter-spacing="2"')
    body += '<circle class="signal" cx="565" cy="32" r="4" fill="#65e5bf"/>' if mobile else '<circle class="signal" cx="1159" cy="32" r="4" fill="#65e5bf"/>'
    for i, (label, value, caption, color) in enumerate(metrics):
        x = 23 + (i % 2) * 283 if mobile else 24 + i * 290
        y = 65 + (i // 2) * 195 if mobile else 63
        w, h = (271, 180) if mobile else (276, 154)
        body += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="13" fill="#14243a" stroke="#2a405d"/>'
        body += text(x+18, y+29, label, 12 if mobile else 11, '#a9bed6', 600, 'letter-spacing=".7"')
        body += text(x+17, y+104 if mobile else y+97, f'{value:,}', 65 if mobile else 61, color, 750)
        body += text(x+19, y+147 if mobile else y+128, caption, 17 if mobile else 13, '#94abc5')
    stamp = datetime.fromisoformat(summary['updated_at'].replace('Z', '+00:00')).strftime('%d %b %Y, %H:%M UTC')
    body += text(27, 481 if mobile else 243, f'Public + private activity  |  Updated {stamp}', 14 if mobile else 12, '#839dbb')
    return svg(width, height, body, 'GitHub contribution totals including public and anonymized private activity')


def heatmap(summary, recent, mobile=False):
    width, height = (600, 270) if mobile else (1200, 310)
    body = text(28, 40, 'ACTIVITY OVER THE PAST YEAR', 17 if mobile else 15, '#96aec9', 600, 'letter-spacing="1.3"')
    body += text(28, 76, f'{summary["past_year_contributions"]:,} contributions', 28, '#e6effc', 650)
    days = sorted(recent['days'], key=lambda d: d['date'])
    first = date.fromisoformat(days[0]['date'])
    offset = (first.weekday() + 1) % 7
    step, size = (10.25, 7.5) if mobile else (21, 15)
    x0, y0 = 28, 111
    months = set()
    for i, day in enumerate(days):
        d = date.fromisoformat(day['date'])
        week, weekday = divmod(i+offset, 7)
        month = (d.year, d.month)
        if month not in months:
            if not mobile or d.month % 2 == 1:
                body += text(x0+week*step, 101, calendar.month_abbr[d.month], 12 if not mobile else 10, '#809ab8')
            months.add(month)
        body += f'<rect x="{x0+week*step:.2f}" y="{y0+weekday*step:.2f}" width="{size}" height="{size}" rx="2" fill="{COLORS[min(day["level"],4)]}"><title>{day["date"]}: {day["count"]} contributions</title></rect>'
    last = days[-1]['date']
    body += text(28, 221 if mobile else 281, f'{days[0]["date"]} to {last}', 16 if mobile else 14, '#91abc6')
    lx = 375 if mobile else 970
    for level, color in enumerate(COLORS):
        body += f'<rect x="{lx+level*19}" y="{235 if mobile else 265}" width="13" height="13" rx="2" fill="{color}"/>'
    body += text(lx-36, 246 if mobile else 277, 'Less', 11, '#91abc6') + text(lx+100, 246 if mobile else 277, 'More', 11, '#91abc6')
    return svg(width, height, body, f'{summary["past_year_contributions"]} contributions over the past year, including anonymized private activity')


def history(summary, mobile=False):
    annual = summary['annual']
    width, row, top = (600, 50, 103) if mobile else (1200, 38, 95)
    height = top + row*len(annual) + 50
    body = text(28, 38, 'CONTRIBUTIONS THROUGH THE YEARS', 17 if mobile else 15, '#98b0cb', 600, 'letter-spacing="1.2"')
    body += text(28, 66, f'{summary["current_year"]} is year to date', 17 if mobile else 14, '#809bb8')
    maximum = max(y['contributions'] for y in annual) or 1
    x, max_width = (118, 378) if mobile else (130, 935)
    for i, entry in enumerate(annual):
        y = top+i*row
        body += text(28, y+19, entry['year'], 22 if mobile else 18, '#d3dfed', 600)
        body += f'<rect x="{x}" y="{y}" width="{max_width}" height="25" rx="6" fill="#172941"/>'
        length = max_width*entry['contributions']/maximum
        if length:
            body += f'<rect x="{x}" y="{y}" width="{length:.2f}" height="25" rx="6" fill="{"#65d9bd" if entry["year"]==summary["current_year"] else "#658fc0"}"/>'
        body += text(x+max_width+18, y+20, entry['contributions'], 21 if mobile else 17, '#c5d7e9', 600)
    body += text(28, height-22, 'GitHub-recorded contributions. Public and private counts combined.', 12 if mobile else 13, '#829dbb')
    return svg(width, height, body, 'Annual GitHub contributions: '+', '.join(f'{y["year"]}: {y["contributions"]}' for y in annual))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, help='Validated browser-captured calendar data for local generation')
    parser.add_argument('--output', type=Path, default=Path('.'))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.input:
        data = json.loads(args.input.read_text())
        today = datetime.fromisoformat(data['collectedAt'].replace('Z','+00:00')).date()
    else:
        today = now.date()
        data = {'collectedAt': now.isoformat(), 'recent': fetch_calendar(), 'years': []}
        for year in range(FIRST_YEAR, today.year+1):
            data['years'].append({**fetch_calendar(year), 'year': year})
    summary = summarize(data, today)
    files = {'contribution-summary.json': json.dumps(summary, indent=2)+'\n'}
    for name, render in [('stats', lambda mobile:stats(summary,mobile)), ('heatmap', lambda mobile:heatmap(summary,data['recent'],mobile)), ('history', lambda mobile:history(summary,mobile))]:
        for mobile in (False, True):
            files[f'contributions-{name}{"-mobile" if mobile else ""}-v1.svg'] = render(mobile)
    # Write only after all source checks and all visual generation have succeeded.
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (args.output/filename).write_text(content)
    readme_path = args.output/'README.md'
    if readme_path.exists():
        readme = readme_path.read_text()
        start, end = '<!-- CONTRIBUTION-YEARS:START -->', '<!-- CONTRIBUTION-YEARS:END -->'
        if start in readme and end in readme:
            links = ' · '.join(f'[{y["year"]}](https://github.com/{USER}?tab=overview&from={y["year"]}-01-01&to={summary["through_date"] if y["year"] == summary["current_year"] else str(y["year"])+"-12-31"})' for y in summary['annual'])
            readme_path.write_text(readme[:readme.index(start)+len(start)]+'\n\n'+links+'\n\n'+readme[readme.index(end):])
    print(json.dumps({k:summary[k] for k in ['lifetime_contributions','current_year_contributions','active_days_this_year','longest_streak_this_year']}))


if __name__ == '__main__':
    main()

import re
import unicodedata

def parse_srt(data):
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
    text=data.decode(encoding).replace('\r\n','\n').strip()
    result=[]
    pattern=re.compile(r'(\d+):(\d{2}):(\d{2})[,.](\d{3})')
    def time(s):
        m=pattern.fullmatch(s.strip())
        if not m:raise ValueError('invalid_srt_time')
        h,mn,sec,ms=map(int,m.groups())
        if mn>59 or sec>59:raise ValueError('invalid_srt_time')
        return h*3600000+mn*60000+sec*1000+ms
    for block in re.split(r'\n[ \t]*\n',text):
        lines=block.splitlines()
        if not lines:continue
        pos=1 if lines[0].strip().isdigit() else 0
        if pos>=len(lines) or '-->' not in lines[pos]:raise ValueError('invalid_srt_block')
        a,b=lines[pos].split('-->',1);start,end=time(a),time(b)
        raw='\n'.join(lines[pos+1:]).strip();clean=re.sub(r'<[^>]*>|\{[^}]*\}','',raw).strip()
        if start<0 or end<=start or not clean:raise ValueError('invalid_srt_cue')
        result.append({'seq':len(result)+1,'start':start,'end':end,'raw':raw,'text':clean})
    if not result:raise ValueError('empty_subtitle')
    return result

def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC',text).lower().split())

def align(cues,duration,offset=0,scale=1):
    # Connected components of CROSS-language overlapping intervals support N:M.
    nodes=[dict(c,language=lang) for lang in ('en','zh') for c in cues[lang]]
    for n in nodes:
        n['start']=round(n['start']*scale+offset);n['end']=round(n['end']*scale+offset)
        if n['start']<0 or n['end']>duration or n['end']<=n['start']:
            raise ValueError('subtitle_out_of_video_bounds')
    parent=list(range(len(nodes)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,a in enumerate(nodes):
        for j in range(i):
            b=nodes[j]
            if a['language']!=b['language'] and min(a['end'],b['end'])>max(a['start'],b['start']):parent[find(i)]=find(j)
    groups={}
    for i,n in enumerate(nodes):groups.setdefault(find(i),[]).append(n)
    result=[]
    for group in groups.values():
        texts={lang:'\n'.join(n['text'] for n in sorted(group,key=lambda x:(x['start'],x['seq'])) if n['language']==lang) for lang in ('en','zh')}
        flags=[]
        if not all(texts.values()):flags.append('missing_language')
        if len(group)>2:flags.append('many_to_many_review')
        start=min(n['start'] for n in group);end=max(n['end'] for n in group)
        if end-start>15000:flags.append('long_segment')
        if all(texts.values()):
            ranges={lang:(min(n['start'] for n in group if n['language']==lang),max(n['end'] for n in group if n['language']==lang)) for lang in texts}
            if max(abs(ranges['en'][i]-ranges['zh'][i]) for i in (0,1))>300:flags.append('boundary_difference')
        result.append({'start':start,'end':end,**texts,'nodes':group,'flags':flags})
    return sorted(result,key=lambda x:(x['start'],x['end']))

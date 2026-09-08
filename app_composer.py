"""Compose a dedicated app, bound to one source's existing data.
The model controls navigation, screen layouts and filters, never executable code,
record contents or connection identity. All pages inherit the server-side binding.
"""
import json
import re
from workspace_sources import SOURCES

LAYOUTS = ['inbox', 'board', 'feed', 'table', 'focus']
FIELDS = ['id', 'title', 'status', 'priority', 'person', 'date']
FOLDERS = ['inbox', 'sent', 'starred', 'all']


def validate_app(candidate, source, prompt):
    if not isinstance(candidate, dict): raise ValueError('App configuration must be an object.')
    pages = candidate.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= 6:
        raise ValueError('An app needs between one and six screens.')
    cleaned = []
    ids = set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get('layout') not in LAYOUTS:
            raise ValueError('Unsupported screen layout.')
        if 'source' in page or 'sources' in page:
            raise ValueError('Screens inherit the app connection; they cannot switch sources.')
        page_id = re.sub(r'[^a-z0-9-]', '-', str(page.get('id') or f'screen-{index + 1}').lower())[:40]
        if page_id in ids: raise ValueError('Screen IDs must be unique.')
        ids.add(page_id)
        columns = page.get('columns', ['title', 'status', 'person', 'date'])
        if not isinstance(columns, list) or not columns or any(c not in FIELDS for c in columns):
            raise ValueError('Unsupported data columns.')
        columns = list(dict.fromkeys(['title', *columns]))
        folder = page.get('folder', 'inbox')
        if folder not in FOLDERS: raise ValueError('Unsupported mail folder.')
        cleaned.append({'id':page_id, 'title':str(page.get('title') or 'My work')[:60],
            'description':str(page.get('description') or '')[:220], 'layout':page['layout'],
            'query':str(page.get('query') or '')[:300], 'status':str(page.get('status') or '')[:60],
            'folder':folder, 'columns':columns})
    return {'title':str(candidate.get('title') or f'My {SOURCES[source]["label"]} app')[:80],
        'description':str(candidate.get('description') or prompt)[:300], 'purpose':prompt[:2000],
        'source':source, 'pages':cleaned,
        'accent':candidate.get('accent') if candidate.get('accent') in ['violet','blue','sage','amber'] else 'violet',
        'density':candidate.get('density') if candidate.get('density') in ['comfortable','compact'] else 'comfortable'}


def starter_app(source, prompt, name='', role=''):
    text = prompt.lower()
    layout = next((l for l in LAYOUTS if l in text), SOURCES[source]['layout'])
    def page(id, title, layout, **kw):
        return dict(id=id,title=title,layout=layout,description='',query='',status='',folder='inbox',columns=['title','status','person','date'],**kw)
    if source == 'gmail':
        pages = [page('inbox','Inbox',layout), page('starred','Starred','inbox'), page('sent','Sent','inbox')]
        pages[1]['folder']='starred'; pages[2]['folder']='sent'
    elif source in ['jira','github','helpdesk']:
        pages = [page('work','My work',layout),page('all','All records','table')]
        if any(word in text for word in ['blocked','urgent','priority']):
            pages.insert(0,page('attention','Needs attention','focus'))
            if source == 'jira': pages[0]['status']='Blocked'
            if source == 'helpdesk': pages[0]['status']='escalated'
    else:
        pages = [page('conversation','Conversation',layout),page('browse','Browse messages','table')]
    return validate_app({'title':name or f'My {SOURCES[source]["label"]}',
        'description':prompt,'pages':pages,'density':'compact' if 'compact' in text else 'comfortable',
        'accent':'sage' if source == 'helpdesk' else 'blue' if source == 'github' else 'violet'},source,prompt)


def compose_app(source, prompt, name, role, records, api_key=None, previous=None):
    if not api_key:
        return starter_app(source,prompt,name,role), 'starter'
    import anthropic
    page_schema = {'type':'object','properties':{
        'id':{'type':'string'},'title':{'type':'string'},'description':{'type':'string'},
        'layout':{'type':'string','enum':LAYOUTS},'query':{'type':'string'},'status':{'type':'string'},
        'folder':{'type':'string','enum':FOLDERS},'columns':{'type':'array','items':{'type':'string','enum':FIELDS}}},
        'required':['id','title','description','layout','query','status','folder','columns'],'additionalProperties':False}
    schema={'type':'object','properties':{'title':{'type':'string'},'description':{'type':'string'},
        'accent':{'type':'string','enum':['violet','blue','sage','amber']},
        'density':{'type':'string','enum':['comfortable','compact']},
        'pages':{'type':'array','minItems':1,'maxItems':6,'items':page_schema}},
        'required':['title','description','accent','density','pages'],'additionalProperties':False}
    # Only the real data shape and categorical states inform composition. Existing
    # records are rendered by the data endpoint, not copied into model output.
    profile={'returned_record_count':len(records),'fields':sorted({k for r in records for k in r if k in FIELDS}),
        'statuses':sorted({r.get('status','') for r in records if r.get('status')})}
    try:
        client=anthropic.Anthropic(api_key=api_key,timeout=30,max_retries=0)
        response=client.messages.create(model='claude-haiku-4-5',max_tokens=1800,
            system='Design a dedicated usable application for one person using ONE connected software source. '
            'Generate purposeful navigation and 1-6 distinct screens from the user goal and REAL data profile. '
            'Do not generate an integration dashboard or one generic grid. Each screen has its own layout and data filter. '
            'inbox=list/detail with actions, board=status columns, feed=message stream, table=chosen columns, focus=record-by-record. '
            'Use only actual statuses in the profile; empty status means all. query is a literal search term, not an instruction. '
            'For Gmail use actual folders (inbox, sent, starred, all); query may use Gmail search syntax. '
            'Never invent fields, data, unsupported write controls or claims about records. '
            'When refining, preserve existing screens unless the request changes them. Role is a layout preference, never a permission.',
            messages=[{'role':'user','content':json.dumps({'source':source,'request':prompt,'app_name':name,'role':role,'data_profile':profile,'previous_app':previous})}],
            tools=[{'name':'build_app','description':'Create a source-bound application with its own screens and navigation','input_schema':schema}],
            tool_choice={'type':'tool','name':'build_app'})
        candidate=next(b.input for b in response.content if b.type=='tool_use')
        spec=validate_app(candidate,source,prompt)
        if name: spec['title']=name[:80]
        valid_statuses=set(profile['statuses'])
        if any(p['status'] and p['status'] not in valid_statuses for p in spec['pages']):
            raise ValueError('The generated filter is absent from the actual data.')
        return spec,'ai'
    except Exception:
        # Failed refinements must not discard an existing user-built app.
        if previous:
            raise ValueError('The app designer is unavailable. Your current app has been kept unchanged. Try again.') from None
        return starter_app(source,prompt,name,role),'starter'

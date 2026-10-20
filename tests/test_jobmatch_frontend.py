"""Execute both classic scripts in Node with a minimal DOM and controlled API.

These exercise state, event delegation and request boundaries, not browser layout.
"""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(id) { this.id=id; this.listeners={}; this.dataset={}; this.value=''; this.innerHTML=''; this.textContent=''; this.disabled=false; this.hidden=false; this.classList={toggle(){}}; }
  addEventListener(type,fn) { (this.listeners[type] ||= []).push(fn); }
  emit(type,target=this) { for (const fn of this.listeners[type] || []) fn({target,preventDefault(){}}); }
  insertAdjacentHTML(_,html) { this.innerHTML += html; }
  querySelectorAll() { return []; }
  closest() { return this; }
}
const elements = new Map(), el = id => { if (!elements.has(id)) elements.set(id,new Element(id)); return elements.get(id); };
const initial = {'city-filter':'All cities','source-filter':'All sources','role-filter':'All role types','experience-filter':'All experience','remote-filter':'All remote types'};
for (const [id,value] of Object.entries(initial)) el(id).value=value;
const storageWrites=[], calls=[], pending=[];
let parseDeferred=false, matchDeferred=false, suggestDeferred=false, providerUnavailable=false;
const profile={skills:['Python'],titles:['Engineer'],years_experience:3,education:[],projects:[],redacted_text:'[redacted]'};
const maliciousJob={title:'<img src=x onerror=alert(1)>',company:'Example',location:'Bengaluru',skills:['Python'],source_url:'javascript:alert(1)',score:70,matched_skills:['Python'],missing_skills:['SQL'],breakdown:{skills:70},reason:'Relevant skills'};
const response = (body,status=200) => ({ok:status<400,status,json:async()=>body});
const context = vm.createContext({console,URL,URLSearchParams,AbortController,setTimeout,clearTimeout,
  window:{},document:{getElementById:el,querySelectorAll:()=>[],querySelector:selector=>el(selector),body:new Element('body')},
  localStorage:{getItem:()=>null,setItem:(...args)=>storageWrites.push(args)},
  FormData:class { constructor(){this.entries=[];} append(...args){this.entries.push(args);} },
  fetch:async (url,options={})=>{
    const path=new URL(url).pathname, body=options.body && typeof options.body==='string'?JSON.parse(options.body):options.body;
    calls.push({url,path,body,options});
    if (path.endsWith('/resume/parse') || path.endsWith('/resume/upload')) {
      if(parseDeferred) return new Promise(resolve=>pending.push(()=>resolve(response({profile}))));
      return response({profile:JSON.parse(JSON.stringify(profile))});
    }
    if (path.endsWith('/resume/match')) {
      if(providerUnavailable) return response({detail:'Embeddings are not configured.'},503);
      const result={items:[maliciousJob],roles:[{role:'Engineer',score:70,jobs:1}],gaps:[{skill:'SQL',count:1,total:1,advice:'Verify your SQL experience.'}],total:1};
      if(matchDeferred) return new Promise(resolve=>pending.push(()=>resolve(response(result))));
      return response(result);
    }
    if (path.endsWith('/resume/suggestions')) {
      const result={suggestions:{keywords_to_add:['<script>unsafe</script>'],bullet_suggestions:['Use measurable outcomes'],section_improvements:['Clarify projects'],learning_suggestions:['Practice SQL']}};
      if(suggestDeferred) return new Promise(resolve=>pending.push(()=>resolve(response(result))));
      return response(result);
    }
    if (path.endsWith('/data-quality')) return response({metadata:{}});
    return response({items:[maliciousJob],total:1,page:1,page_size:20});
  }
});
const root=el('view-root');
const run = code => vm.runInContext(code,context);
const tick = async()=>{for(let i=0;i<12;i++) await Promise.resolve();};
const click = action=>{const target=new Element('button');target.dataset.jmAction=action;root.emit('click',target);};
const input = (key,value,field=false)=>{const target=new Element('input');target.dataset[field?'jmField':'jmInput']=key;target.value=value;if(typeof value==='boolean'){target.type='checkbox';target.checked=value;}root.emit('input',target);};
const navigate = name=>run(`setView(${JSON.stringify(name)})`);
const parse = async()=>{navigate('Upload Resume');input('text','Python engineer with three years experience');click('parse-text');await tick();};
vm.runInContext(fs.readFileSync('frontend/app.js','utf8'),context,{filename:'app.js'});
vm.runInContext(fs.readFileSync('frontend/jobmatch.js','utf8'),context,{filename:'jobmatch.js'});
"""


def run_scenario(source):
    if not NODE:
        pytest.skip("Node is required for the frontend runtime checks")
    result = subprocess.run(
        [NODE, "-e", HARNESS + "\n(async()=>{\n" + source +
         "\n})().catch(error=>{console.error(error);process.exitCode=1;});"],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_script_order_navigation_default_and_background_editor_focus():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert html.index('src="app.js"') < html.index('src="jobmatch.js"')
    run_scenario(r"""
      await tick();
      assert.equal(run('view'),'Bengaluru Jobs');
      assert.equal(run('NAV.length'),11);
      for(const name of ['Overview','Skills','Locations','Salaries','Learn next']) assert(run(`NAV.some(([n])=>n===${JSON.stringify(name)})`));
      assert.equal(el('navigation').listeners.click.length,1);
      assert(calls.some(call=>call.path.endsWith('/jobs/bengaluru')));
      assert(!root.innerHTML.includes('href="javascript:'));
      assert(root.innerHTML.includes('&lt;img'));
      navigate('Upload Resume');
      const before=root.innerHTML;
      input('text','typing without focus replacement');
      run('render()');
      assert.equal(root.innerHTML,before);
      navigate('Overview');
      assert.equal(run('view'),'Overview');
      assert(!root.innerHTML.includes('jm-session'));
    """)


def test_parse_review_baseline_match_roles_gaps_and_no_persistence():
    run_scenario(r"""
      await tick();await parse();
      assert(root.innerHTML.includes('Review your extracted profile'));
      input('skills','Python, SQL',true); input('titles','Data Engineer',true); input('years_experience','4',true);
      navigate('My Matches');click('match');await tick();
      const request=calls.find(call=>call.path.endsWith('/resume/match'));
      assert.deepEqual(request.body.profile.skills,['Python','SQL']);
      assert.equal(request.body.profile.years_experience,4);
      assert.equal(request.body.use_embeddings,false); assert.equal(request.body.consent_external,false);
      assert.equal(request.body.limit,50);
      assert(root.innerHTML.includes('70.0 / 100'));
      assert(!root.innerHTML.includes('href="javascript:'));
      navigate('Role Fit');assert(root.innerHTML.includes('Engineer'));
      navigate('Skill Gaps');assert(root.innerHTML.includes('Verify your SQL experience.'));
      navigate('Upload Resume');input('skills','Python',true);
      navigate('Skill Gaps');assert(root.innerHTML.includes('No match results yet'));
      assert.equal(storageWrites.length,0);
    """)


def test_external_ai_requires_explicit_request_and_consent_and_reports_503():
    run_scenario(r"""
      await tick();await parse();navigate('My Matches');input('embeddings',true);click('match');await tick();
      assert(!calls.some(call=>call.path.endsWith('/resume/match')));
      assert(root.innerHTML.includes('require explicit consent'));
      input('matchConsent',true);providerUnavailable=true;click('match');await tick();
      assert(!root.innerHTML.includes('unavailable (503)'));
      assert(root.innerHTML.includes('External AI is not configured on the server'));
      assert(root.innerHTML.includes('Baseline matching remains available'));
      navigate('Resume Suggestions');
      assert(!calls.some(call=>call.path.endsWith('/resume/suggestions')));
      click('suggest');await tick();assert(!calls.some(call=>call.path.endsWith('/resume/suggestions')));
      input('suggestionConsent',true);input('target','ML Engineer');click('suggest');await tick();
      const req=calls.find(call=>call.path.endsWith('/resume/suggestions'));
      assert.equal(req.body.consent_external,true);assert.equal(req.body.target_role,'ML Engineer');
      assert(root.innerHTML.includes('&lt;script&gt;unsafe&lt;/script&gt;'));
      const count=calls.length;click('suggest');await tick();assert.equal(calls.length,count);
    """)


@pytest.mark.parametrize("operation", ["parse", "match", "suggest"])
def test_clear_invalidates_late_private_results(operation):
    setup = {
        "parse": "parseDeferred=true;navigate('Upload Resume');input('text','private resume');click('parse-text');",
        "match": "await parse();matchDeferred=true;navigate('My Matches');click('match');",
        "suggest": "await parse();suggestDeferred=true;navigate('Resume Suggestions');input('suggestionConsent',true);click('suggest');",
    }[operation]
    run_scenario("await tick();" + setup + r"""
      await tick();assert.equal(pending.length,1);click('clear');pending.shift()();await tick();
      assert(root.innerHTML.includes('Session cleared'));
      navigate('Upload Resume');assert(root.innerHTML.includes('No parsed resume yet'));
      assert(!root.innerHTML.includes('private resume'));
      navigate('My Matches');assert(!root.innerHTML.includes('70.0 / 100'));
      navigate('Resume Suggestions');assert(!root.innerHTML.includes('Use measurable outcomes'));
      assert.equal(storageWrites.length,0);
    """)


def test_file_validation_upload_and_catalog_search_contract():
    run_scenario(r"""
      await tick();navigate('Upload Resume');
      const fileInput=new Element('jm-file');fileInput.files=[{name:'resume.exe',size:10}];root.emit('change',fileInput);
      assert(root.innerHTML.includes('non-empty PDF or DOCX'));
      fileInput.files=[{name:'resume.pdf',size:5*1024*1024+1}];root.emit('change',fileInput);
      click('parse-file');await tick();assert(!calls.some(call=>call.path.endsWith('/resume/upload')));
      fileInput.files=[{name:'resume.docx',size:100}];root.emit('change',fileInput);click('parse-file');await tick();
      const upload=calls.find(call=>call.path.endsWith('/resume/upload'));
      assert.equal(upload.body.entries[0][0],'file');assert.equal(upload.options.headers,undefined);
      navigate('Bengaluru Jobs');input('query','Python & RAG');
      const form=new Element('jm-search');root.emit('submit',form);await tick();
      assert(calls.some(call=>new URL(call.url).searchParams.get('q')==='Python & RAG'));
      const scope=new Element('jm-scope');scope.value='ai';root.emit('change',scope);await tick();
      assert(calls.some(call=>call.path.endsWith('/jobs/ai')));
    """)


def test_multisource_filters_and_match_source_contract():
    run_scenario(r"""
      await tick();
      for (const name of ['Greenhouse','Lever','Ashby']) assert(root.innerHTML.includes(name));
      input('source','lever');input('company','Meesho');input('role','Software');input('skillQuery','Python, SQL');
      root.emit('submit',new Element('jm-search'));await tick();
      const request=calls.filter(call=>call.path.endsWith('/jobs/bengaluru')).at(-1);
      const params=new URL(request.url).searchParams;
      assert.equal(params.get('source'),'lever');assert.equal(params.get('company'),'Meesho');
      assert.equal(params.get('role'),'Software');assert.equal(params.get('skills'),'Python, SQL');
      click('reset-filters');await tick();
      const reset=new URL(calls.filter(call=>call.path.endsWith('/jobs/bengaluru')).at(-1).url);
      assert.equal(reset.searchParams.get('source'),'');assert.equal(reset.searchParams.get('skills'),'');
      await parse();navigate('My Matches');input('matchSource','ashby');click('match');await tick();
      assert.equal(calls.filter(call=>call.path.endsWith('/resume/match')).at(-1).body.source,'ashby');
      input('matchSource','lever');assert(!root.innerHTML.includes('70.0 / 100'));
    """)

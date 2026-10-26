/* Session-only resume workspace, layered over the original dashboard. */
(() => {
  'use strict';
  const views = [['Bengaluru Jobs','⌖'],['Upload Resume','↑'],['My Matches','◎'],['Role Fit','◇'],['Skill Gaps','✦'],['Resume Suggestions','↗']];
  NAV.unshift(...views);
  const state = {text:'',file:null,profile:null,matches:null,suggestions:null,target:'',embeddings:false,lastMatchEmbeddings:false,matchConsent:false,suggestionConsent:false,busy:'',error:'',notice:'',query:'',source:'',company:'',role:'',skillQuery:'',matchSource:'',scope:'bengaluru',includeStale:false,freshnessDays:30,metadata:null,page:1,catalog:null,catalogBusy:false,catalogError:''};
  function requestedScope(){const scope=new URLSearchParams(window.location?.search||'').get('scope');return scope==='india'||scope==='ai'||scope==='bengaluru'?scope:'bengaluru';}
  function readShareState(){const params=new URLSearchParams(window.location?.search||''),scope=requestedScope();if(scope==='india')state.scope='bengaluru';else state.scope=scope;if(params.get('q'))state.query=params.get('q');if(params.get('source'))state.source=params.get('source');if(params.get('company'))state.company=params.get('company');if(params.get('role'))state.role=params.get('role');if(params.get('skills'))state.skillQuery=params.get('skills');state.includeStale=params.get('stale')==='1';const page=Number(params.get('page'));if(Number.isInteger(page)&&page>0)state.page=page;}
  function writeShareState(push=false){if(!window.history?.replaceState||!window.location)return;const params=new URLSearchParams(window.location.search||'');['scope','q','source','company','role','skills','stale','page'].forEach(key=>params.delete(key));params.set('scope',view==='Overview'?'india':state.scope);if(view!=='Overview'){if(state.query)params.set('q',state.query);if(state.source)params.set('source',state.source);if(state.company)params.set('company',state.company);if(state.role)params.set('role',state.role);if(state.skillQuery)params.set('skills',state.skillQuery);if(state.includeStale)params.set('stale','1');if(state.page>1)params.set('page',String(state.page));}const query=params.toString(),url=`${window.location.pathname}${query?`?${query}`:''}${window.location.hash||''}`;(push?window.history.pushState:window.history.replaceState).call(window.history,null,'',url);}
  let generation = 0, catalogRequest = 0, paintedView = '';
  const e = escapeHTML, arr = value => Array.isArray(value) ? value : [], words = value => arr(value).join(', ');
  const split = value => value.split(',').map(x => x.trim()).filter(Boolean);
  const button = (action,label,primary=false,disabled=false) => `<button type="button" class="${primary?'primary':'secondary'}" data-jm-action="${action}" ${disabled?'disabled':''}>${label}</button>`;
  const empty = message => `<p class="empty">${e(message)}</p>`;
  const safeURL = value => { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch (_) { return ''; } };
  const score = value => Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} / 100` : 'Not scored';
  const profileReady = () => state.profile && typeof state.profile === 'object';
  const dateLabel = (value, unknown='Unknown') => { if (!value) return unknown; const date = new Date(value); return Number.isNaN(date.getTime()) ? unknown : date.toLocaleDateString([], {dateStyle:'medium'}); };
  const verificationStatus = job => { const raw=String(job.verification_status||job.status||'').toLowerCase(); if(raw.includes('closed')) return 'Closed'; if(raw.includes('checked')||job.source_checked_at) return 'Source checked'; return String(job.freshness_status||'').toLowerCase()==='current'?'Recent':'Unknown'; };
  const freshness = job => { const status = String(job.freshness_status || (Number.isFinite(Number(job.age_days)) ? (Number(job.age_days) <= state.freshnessDays ? 'current' : 'stale') : 'unknown')).toLowerCase(),verification=verificationStatus(job); const age = Number.isFinite(Number(job.age_days)) ? `${job.age_days} day${Number(job.age_days)===1?'':'s'}` : 'unknown'; const verified = dateLabel(job.last_verified || job.collected_at); return `<span class="freshness-badge freshness-${e(status)}">${e(status==='current'?'Recent':status==='stale'?'Stale':'Date unknown')}</span><span class="verification-status verification-${e(verification.toLowerCase().replace(/\s+/g,'-'))}">Status: ${e(verification)}</span><span class="freshness-detail">Age: ${e(age)} · Posted: ${e(dateLabel(job.posted_date || job.posted,'Unknown'))} · Last verified/collected: ${e(verified)} · Confirm still open at source</span>`; };
  const coverage = () => { const metadata = state.metadata || {}, scope = metadata.dataset_scope || {}, facets = state.catalog?.facets || {}, companies = scope.company_count ?? metadata.company_count ?? (facets.companies || []).length, sources = metadata.source_count ?? scope.source_count ?? (facets.sources || []).length; return `Coverage: ${metadata.dataset_rows ?? scope.row_count ?? 'configured'} dataset rows · ${companies || 'configured'} companies · ${sources || 'configured'} sources${scope.locations?.length ? ` · ${scope.locations.length} locations` : ''} · configured public boards only, not the full market`; };
  const scopeMessage = () => `${state.catalog ? `${state.catalog.total ?? arr(state.catalog.items).length} current roles in the Bengaluru feed. ` : ''}${state.scope==='ai'?'Bengaluru AI Jobs':'Bengaluru Jobs'} is a Bengaluru-only feed of explicit Bengaluru tech roles. This is a small, directional sample, not a complete market view. India Insights is the broader configured India dataset, not the full Indian job market. ${coverage()}`;
  const sourceOptions = selected => ['','greenhouse','lever','ashby'].map(source => `<option value="${source}" ${source===selected?'selected':''}>${source?e(source[0].toUpperCase()+source.slice(1)):'All sources'}</option>`).join('');
  function structured(value) {
    if (value === null || value === undefined) return '';
    if (Array.isArray(value)) return `<ul>${value.map(item => `<li>${structured(item)}</li>`).join('')}</ul>`;
    if (typeof value === 'object') return `<dl>${Object.entries(value).map(([key,item]) => `<dt>${e(key.replace(/_/g,' '))}</dt><dd>${structured(item)}</dd>`).join('')}</dl>`;
    return e(value);
  }
  function cards(items,matched=false) {
    return arr(items).map(job => {
      const url = safeURL(job.source_url || job.sourceUrl || job.url);
      return `<article class="jm-job"><div class="jm-job-heading"><div><h3>${e(job.title || 'Untitled role')}</h3><p>${e(job.company || 'Company not listed')} · ${e(job.location || job.city || 'Location not listed')}</p></div>${matched?`<strong class="jm-score">${e(score(job.score))}</strong>`:''}</div><p>${e(job.source || 'Source not listed')} · ${e(job.remote_type === 'Unknown' || !job.remote_type ? 'Not provided by employer' : job.remote_type)}</p><p class="job-freshness">${freshness(job)}</p><p>${e(words(job.skills) || 'Not provided by employer')}</p>${matched?`<p><b>Matched skills:</b> ${e(words(job.matched_skills) || 'None identified')}</p><p><b>Not identified in your profile:</b> ${e(words(job.missing_skills) || 'None identified')} — verify and correct your profile.</p><p>${e(job.reason || '')}</p><details><summary>Score breakdown</summary>${structured(job.breakdown || {})}</details>`:''}${url?`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">View source listing ↗</a>`:'<span class="form-status">No secure source link available</span>'}</article>`;
    }).join('');
  }
  function profileEditor() {
    if (!profileReady()) return empty('No parsed resume yet. Paste text or upload a file, then parse it to review your profile.');
    const p = state.profile;
    return `<section class="panel"><h2>Review your extracted profile</h2><p class="panel-help">Extraction can miss details. Correct skills, titles and experience manually before matching. A skill not identified here does not mean you lack it.</p><fieldset ${state.busy?'disabled':''}><div class="jm-fields"><label>Skills (comma-separated)<textarea data-jm-field="skills">${e(words(p.skills))}</textarea></label><label>Titles (comma-separated)<textarea data-jm-field="titles">${e(words(p.titles))}</textarea></label><label>Years of experience<input data-jm-field="years_experience" type="number" min="0" max="80" step="0.5" value="${e(p.years_experience ?? '')}"></label></div></fieldset><details><summary>Extracted education, projects and redacted text</summary>${structured({education:p.education,projects:p.projects,redacted_text:p.redacted_text})}</details><div class="jm-actions">${button('nav-matches','Continue to My Matches',true)}</div></section>`;
  }
  function uploadView() {
     return `<section class="panel"><h2>Upload or paste your resume</h2><p class="panel-help">PDF or DOCX, up to 5 MiB. The selected file or pasted text is sent to this application API only for the requested parse or match action. Raw resume data is not intentionally saved by the app and the response remains in this browser tab; normal hosting logs follow provider retention rules. External AI providers are never used automatically and require explicit opt-in.</p><fieldset ${state.busy?'disabled':''}><label for="jm-text">Resume text</label><textarea id="jm-text" data-jm-input="text" rows="9" placeholder="Paste your resume text here…">${e(state.text)}</textarea><div class="jm-actions">${button('parse-text','Parse pasted text',true,!state.text.trim() || !!state.busy)}</div><label for="jm-file">Or choose a PDF / DOCX file</label><input id="jm-file" type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"><p id="jm-file-label" class="form-status">${e(state.file?.name || 'No file selected')}</p>${button('parse-file','Parse selected file',true,!state.file || !!state.busy)}</fieldset></section>${profileEditor()}`;
  }
  function matchControls() {
    return `<section class="panel"><h2>Compare your profile with current jobs</h2><p class="panel-help">Baseline matching uses your reviewed profile across all configured sources. Local semantic matching runs without an API key; scores indicate relative fit, not hiring probability.</p>${profileReady()?`<p>${e(words(state.profile.skills) || 'No skills identified — review your profile first.')}</p><fieldset ${state.busy?'disabled':''}><label>Match job source<select data-jm-input="matchSource">${sourceOptions(state.matchSource)}</select></label><label class="jm-check"><input type="checkbox" data-jm-input="embeddings" ${state.embeddings?'checked':''}> Use local semantic matching (no API key)</label>${button('match','Find my matches',true,!!state.busy)}</fieldset>`:`${empty('No parsed resume yet. Parse and review a resume to find matches.')}${button('nav-upload','Upload Resume',true)}`}</section>`;
  }
  function resultsView() {
    const result = state.matches;
    if (view === 'My Matches') return `${matchControls()}${result?`<section class="panel"><h2>${e(result.total ?? arr(result.items).length)} matching roles</h2><p class="panel-help">${e(state.lastMatchEmbeddings?'Requested embedding-enhanced matching.':'Baseline matching.')} How matching works: scores compare reviewed profile skills with extracted role requirements; missing skills are shown below. Scores are comparative, not hiring probabilities. ${e(typeof result.integration === 'string'?result.integration:'')}</p>${cards(result.items,true) || empty('No matching jobs found. Review your profile or try again when more listings are available.')}</section>`:''}`;
    if (!result) return `<section class="panel"><h2>${e(view)}</h2>${empty('No match results yet. Parse a resume, review the profile, and run My Matches first.')}${button('nav-matches','Go to My Matches',true)}</section>`;
    if (view === 'Role Fit') return `<section class="panel"><h2>Role fit across matched jobs</h2><p class="panel-help">Comparative scores are not hiring probabilities.</p>${arr(result.roles).map(role=>`<article class="jm-job"><h3>${e(role.role)}</h3><p>${e(score(role.score))} · ${e(typeof role.jobs === 'number'?role.jobs:arr(role.jobs).length)} jobs</p></article>`).join('') || empty('No role-level fit data returned.')}</section>`;
    return `<section class="panel"><h2>Skills to verify and develop</h2><p class="panel-help">These skills were requested in matched jobs but not identified in your reviewed profile. You may already know them; update your profile before treating them as learning needs.</p>${arr(result.gaps).map(gap=>`<article class="jm-job"><h3>${e(gap.skill)}</h3><p>${e(gap.count)} of ${e(gap.total)} relevant jobs</p><p>${e(gap.advice || 'Verify your experience with this skill, then consider targeted practice.')}</p></article>`).join('') || empty('No skill gaps identified in these results.')}</section>`;
  }
  function suggestionsView() {
    return `<section class="panel"><h2>Resume suggestions · grounded and local</h2><p class="panel-help">Suggestions use your reviewed profile and current job-market skills locally. They never invent experience. A paid external AI provider remains optional.</p>${profileReady()?`<fieldset ${state.busy?'disabled':''}><label for="jm-target">Target role (optional)</label><input id="jm-target" data-jm-input="target" value="${e(state.target)}" placeholder="e.g. Data Engineer">${button('suggest','Generate grounded suggestions',true,!!state.busy)}</fieldset>`:`${empty('No parsed resume yet. Upload and review a resume first.')}${button('nav-upload','Upload Resume',true)}`}${state.suggestions?`<div class="jm-suggestions">${structured(state.suggestions.suggestions ?? state.suggestions)}</div>`:''}</section>`;
  }
  function catalogView() {
    const c = state.catalog;
    const filters = `<label>Source<select data-jm-input="source">${sourceOptions(state.source)}</select></label><label>Company<input data-jm-input="company" maxlength="200" value="${e(state.company)}" placeholder="e.g. Meesho"></label><label>Role<input data-jm-input="role" maxlength="200" value="${e(state.role)}" placeholder="e.g. Data Science"></label><label>All these skills<input data-jm-input="skillQuery" maxlength="500" value="${e(state.skillQuery)}" placeholder="Python, SQL"></label>`;
    return `<section class="panel"><h2>Bengaluru Tech Jobs</h2><p class="panel-help"><strong>Current postings only by default:</strong> older than the configured ${e(state.freshnessDays)}-day freshness window, or without a posting date, are hidden. Jobs come from tracked Greenhouse, Lever and Ashby company boards; coverage is limited to configured companies, not every Bengaluru opening. ${e(scopeMessage())}</p><div class="jm-actions">${button('nav-upload','Upload resume →',true)}</div><form id="jm-search"><fieldset ${state.catalogBusy?'disabled':''}><div class="jm-search"><label for="jm-query">Search title, company or description<input id="jm-query" data-jm-input="query" type="search" maxlength="200" value="${e(state.query)}" placeholder="Python, engineer, company…"></label><label for="jm-scope">Job collection<select id="jm-scope"><option value="bengaluru" ${state.scope==='bengaluru'?'selected':''}>Bengaluru tech jobs</option><option value="ai" ${state.scope==='ai'?'selected':''}>Bengaluru AI jobs</option></select></label></div><label class="jm-check stale-toggle"><input type="checkbox" data-jm-input="includeStale" ${state.includeStale?'checked':''}> Include older or unknown-date listings (opt in)</label><div class="jm-fields">${filters}</div><div class="jm-actions"><button class="primary" type="submit">Search / apply filters</button>${button('reset-filters','Clear filters')}</div></fieldset></form><div role="status" aria-live="polite">${state.catalogBusy?(state.includeStale?'Loading current and older listings…':'Loading current listings…'):state.catalogError?e(state.catalogError):c?`${e(c.total ?? arr(c.items).length)} jobs · ${state.includeStale?'including older/unknown-date listings':'current-only'} · Page ${e(state.page)}`:'Ready to search.'}</div>${state.catalogBusy?'':cards(c?.items) || (!state.catalogError?empty('No jobs found for this search. Try a broader query.'):'')}<div class="jm-actions">${button('previous','Previous',false,state.catalogBusy || state.page<=1)}${button('next','Next',false,state.catalogBusy || !c || state.page*Number(c.page_size || 20)>=Number(c.total || 0))}${button('catalog-refresh','Refresh listings',false,state.catalogBusy)}</div></section>`;
  }
  function paint() {
    if (!views.some(([name])=>name===view)) return;
    paintedView = view;
    $('page-title').textContent = view;
    $('role-count').textContent = state.catalog ? String(state.catalog.total ?? arr(state.catalog.items).length) : '—';
     $('view-root').innerHTML = `<div class="jobmatch"><div class="jm-session"><p>Resume text and files are sent to this application API only for the requested action, then kept in this browser tab's memory for the session. The app does not intentionally save raw resumes; hosting-provider logs follow their retention rules. External AI is never used automatically and requires explicit opt-in. Clear / delete session removes the local resume, profile and results.</p>${button('clear','Clear / delete session')}</div><p class="jm-status" role="status" aria-live="polite">${e(state.busy ? `${state.busy}…` : state.notice)}</p>${state.error?`<p class="jm-error" role="alert">${e(state.error)}</p>`:''}${view==='Bengaluru Jobs'?catalogView():view==='Upload Resume'?uploadView():view==='Resume Suggestions'?suggestionsView():resultsView()}</div>`;
      const scopeBanner=$('scope-banner');if(scopeBanner)scopeBanner.innerHTML=e(view==='Bengaluru Jobs'?scopeMessage():'India Insights is the broader configured dataset, not the full Indian job market. Bengaluru Jobs is a separate Bengaluru-only feed.');const bengaluruTab=$('scope-bengaluru'),indiaTab=$('scope-india');if(bengaluruTab&&indiaTab){const bengaluru=view==='Bengaluru Jobs';bengaluruTab.classList.toggle('active',bengaluru);indiaTab.classList.toggle('active',!bengaluru);bengaluruTab.ariaSelected=String(bengaluru);indiaTab.ariaSelected=String(!bengaluru);}
    toggleNav();
  }
  async function request(path,options,external=false) {
    const response = await fetchAPI(path,options);
    if (!response.ok) {
      let detail = ''; try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : ''; } catch (_) {}
      if(response.status===503 && external){const error=Error('External AI is not configured on the server. Baseline matching remains available without embeddings.');error.externalUnavailable=true;throw error;}
      throw Error(`Request failed (${response.status}). ${detail || 'Please retry when the API is available.'}`);
    }
    return response.json();
  }
  const post = (path,body) => request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},body.use_embeddings===true || path==='/resume/suggestions');
  async function loadCatalog() {
    const id = ++catalogRequest, epoch = generation;
    writeShareState();
    state.catalogBusy = true; state.catalogError = ''; paint();
    try {
       const data = await request(`/jobs/${state.scope}?${new URLSearchParams({q:state.query,source:state.source,company:state.company,role:state.role,skills:state.skillQuery,page:String(state.page),page_size:'20',include_stale:String(state.includeStale)})}`);
      if (id !== catalogRequest || epoch !== generation) return;
      state.catalog = data;
    } catch (error) { if (id !== catalogRequest || epoch !== generation) return; state.catalog = null; state.catalogError = error.message || 'Listings are unavailable. Please retry.'; }
    if (id === catalogRequest && epoch === generation) { state.catalogBusy = false; paint(); }
  }
  async function loadMetadata() {
    try { const data = await request('/data-quality'); state.metadata = data.metadata || data; state.freshnessDays = Number(data.freshness_cutoff_days ?? state.metadata.freshness_cutoff_days) || 30; if (paintedView === 'Bengaluru Jobs') paint(); }
    catch (_) { /* Coverage metadata is supplementary; listings remain usable. */ }
  }
  async function run(kind) {
    if (state.busy) return;
    state.error = ''; state.notice = '';
    if (kind==='parse-text' && !state.text.trim()) { state.error='Paste resume text first.'; paint(); return; }
    if (kind==='parse-file' && !state.file) { state.error='Choose a PDF or DOCX first.'; paint(); return; }
    if ((kind==='match' || kind==='suggest') && !profileReady()) { state.error='Parse and review a resume first.'; paint(); return; }
    if (kind==='match' && state.embeddings && state.matchConsent) { state.matchConsent=false; }
    const epoch = generation;
    if (kind==='match') state.matches=null;
    if (kind==='suggest') state.suggestions=null;
    state.busy = kind.startsWith('parse')?'Parsing resume':kind==='match'?'Matching profile':'Requesting suggestions';
    let promise;
    if (kind==='parse-text') promise = post('/resume/parse',{resume_text:state.text});
    if (kind==='parse-file') { const form = new FormData(); form.append('file',state.file); promise = request('/resume/upload',{method:'POST',body:form}); }
    if (kind==='match') { state.lastMatchEmbeddings=state.embeddings; promise = post('/resume/match',{profile:state.profile,source:state.matchSource,use_embeddings:state.embeddings,consent_external:state.embeddings && state.matchConsent,limit:50}); }
    if (kind==='suggest') promise = post('/resume/suggestions',{profile:state.profile,consent_external:false,target_role:state.target});
    state.matchConsent = false; state.suggestionConsent = false; paint();
    try {
      const result = await promise;
      if (epoch !== generation) return;
      if (kind.startsWith('parse')) {
        if (!result.profile || typeof result.profile !== 'object' || Array.isArray(result.profile)) throw Error('The parser returned no usable profile. Try pasting plain text.');
        state.profile = result.profile; state.matches = null; state.suggestions = null; state.notice='Resume parsed. Review and correct your extracted profile below.';
      } else if (kind==='match') { state.matches=result; state.notice='Matching complete. Explore My Matches, Role Fit and Skill Gaps.'; }
      else { state.suggestions=result; state.notice='Suggestions received. Verify them before editing your resume.'; }
    } catch (error) { if (epoch !== generation) return; if(error.externalUnavailable){state.error='';state.embeddings=false;state.matchConsent=false;state.suggestionConsent=false;state.notice='External AI is not configured on the server. Baseline matching remains available — turn off hosted embeddings and run matching again.';}else if(error?.name==='AbortError'){state.error='Semantic matching took too long to respond. The server may be waking up; try again in a moment, or turn off semantic matching for immediate baseline matching.';}else state.error=error.message || 'The service is unavailable. Please retry.'; }
    finally { if (epoch === generation) { state.busy=''; paint(); } }
  }
  function clearSession() {
    generation++; catalogRequest++;
    Object.assign(state,{text:'',file:null,profile:null,matches:null,suggestions:null,target:'',embeddings:false,lastMatchEmbeddings:false,matchConsent:false,suggestionConsent:false,matchSource:'',busy:'',error:'',notice:'Session cleared. Resume, file, profile and results deleted from this tab.',catalogBusy:false});
    paint();
  }
  const originalRender = render;
  render = function() {
    const matcher = views.some(([name])=>name===view);
    document.body.classList.toggle('jobmatch-active',matcher);
    if (!matcher) { paintedView=''; originalRender(); return; }
    // Background dashboard loads must not replace an editor while the user types.
    if (paintedView !== view) paint();
  };
  $('scope-bengaluru')?.addEventListener('click',()=>{ state.scope='bengaluru'; $('scope-bengaluru').classList.toggle('active',true); $('scope-india').classList.toggle('active',false); $('scope-bengaluru').ariaSelected='true'; $('scope-india').ariaSelected='false'; setView('Bengaluru Jobs'); writeShareState(true); loadCatalog(); });
  $('scope-india')?.addEventListener('click',()=>{ $('scope-bengaluru').classList.toggle('active',false); $('scope-india').classList.toggle('active',true); $('scope-bengaluru').ariaSelected='false'; $('scope-india').ariaSelected='true'; setView('Overview'); writeShareState(true); });
  $('view-root').addEventListener('input',event => {
    const target = event.target, key = target.dataset.jmInput, field = target.dataset.jmField;
    if (key) {
      state[key] = target.type==='checkbox' ? target.checked : target.value;
      if (key==='matchSource') { state.matches=null; state.matchConsent=false; paint(); }
      if (key==='text') {
        const b = document.querySelector('[data-jm-action="parse-text"]'); if (b) b.disabled=!state.text.trim();
      }
    }
    if (field && profileReady()) {
      state.profile[field] = field==='years_experience' ? (target.value===''?null:Number(target.value)) : split(target.value);
      state.matches=null; state.suggestions=null; state.matchConsent=false; state.suggestionConsent=false;
    }
  });
   $('view-root').addEventListener('change',event => {
      if (event.target.id==='jm-scope') { state.scope=event.target.value; state.page=1; state.catalog=null; loadCatalog(); }
      if (event.target.dataset.jmInput==='includeStale') { state.includeStale=!!event.target.checked; state.page=1; state.catalog=null; loadCatalog(); }
    if (event.target.id!=='jm-file') return;
    const file = event.target.files?.[0]; state.file=null; state.error='';
    if (file && (!/\.(pdf|docx)$/i.test(file.name) || file.size>5*1024*1024 || file.size===0)) state.error='Choose a non-empty PDF or DOCX file no larger than 5 MiB.';
    else if (file) state.file=file;
    paint();
  });
  $('view-root').addEventListener('submit',event => { if (event.target.id==='jm-search') { event.preventDefault(); state.page=1; loadCatalog(); } });
  $('view-root').addEventListener('click',event => {
    const control = event.target.closest('[data-jm-action]'); if (!control || control.disabled) return;
    const action = control.dataset.jmAction;
    if (action==='clear') clearSession();
    else if (action==='nav-upload') setView('Upload Resume');
    else if (action==='nav-matches') setView('My Matches');
    else if (action==='previous' || action==='next') { state.page += action==='next'?1:-1; loadCatalog(); }
    else if (action==='catalog-refresh') loadCatalog();
     else if (action==='reset-filters') { Object.assign(state,{query:'',source:'',company:'',role:'',skillQuery:'',page:1}); loadCatalog(); }
    else run(action);
  });
   cards = function(items,matched=false) {
     return arr(items).map(job => {
       const url = safeURL(job.source_url || job.sourceUrl || job.url);
       return `<article class="jm-job"><div class="jm-job-heading"><div><h3>${e(job.title || 'Untitled role')}</h3><p>${e(job.company || 'Company not listed')} · ${e(locationLabel(job))}</p></div>${matched?`<strong class="jm-score">${e(score(job.score))}</strong>`:''}</div><p>${e(job.source || 'Source not listed')} · ${e(job.remote_type === 'Unknown' || !job.remote_type ? 'Not provided by employer' : job.remote_type)}</p><p class="job-freshness">${freshness(job)}</p><p>${e(words(job.skills) || 'Not provided by employer')}</p>${matched?`<p><b>Matched skills:</b> ${e(words(job.matched_skills) || 'None identified')}</p><p><b>Not identified in your profile:</b> ${e(words(job.missing_skills) || 'None identified')} — verify and correct your profile.</p><p>${e(job.reason || '')}</p><details><summary>Score breakdown</summary>${structured(job.breakdown || {})}</details>`:''}${url?`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">View source listing ↗</a>`:'<span class="form-status">No secure source link available</span>'}</article>`;
     }).join('');
   };
   // setup() has already registered the nav's delegated click listener.
  // Replace only its children; keep that listener and preserve the five legacy views.
  $('navigation').innerHTML = NAV.map(([name,icon])=>`<button type="button" data-view="${e(name)}"><i>${e(icon)}</i>${e(name)}</button>`).join('');
   const baseSetView=setView;
   setView=function(next,options={}){baseSetView(next);if(!options.skipUrl)writeShareState(options.push===true);};
   function applyScope(scope,push=false){const normalized=scope==='india'||scope==='ai'||scope==='bengaluru'?scope:'bengaluru',india=normalized==='india',same=india?view==='Overview':view==='Bengaluru Jobs'&&state.scope===normalized;if(!same){if(!india)state.scope=normalized;setView(india?'Overview':'Bengaluru Jobs',{skipUrl:true});if(!india)loadCatalog();}writeShareState(push);}
   window.__BIZCHAT_APPLY_SCOPE=applyScope;
   window.addEventListener?.('popstate',()=>{readShareState();applyScope(requestedScope());});
   readShareState();
   applyScope(requestedScope());
   loadMetadata();
 })();

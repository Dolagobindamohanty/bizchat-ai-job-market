"""Executable Node regressions for dashboard location tags and table controls.

Uses the shared minimal DOM, including both shipped scripts; this does not test
browser layout or native select behavior.
"""
from test_jobmatch_frontend import run_scenario


def test_location_aliases_boundaries_and_multi_city_tags():
    run_scenario(r"""
      await tick();
      const cases = {
        'Bangalore':['Bengaluru'], 'Bengaluru':['Bengaluru'], 'BLR':['Bengaluru'],
        'BANGALORE, Karnataka, India':['Bengaluru'],
        'Bengaluru - Manyata Tech Park campus':['Bengaluru'],
        'Bangalore (Electronic City Campus)':['Bengaluru'],
        'Remote - Bengaluru':['Bengaluru'],
        'Bangalore / Hyderabad':['Bengaluru','Hyderabad'],
        'Mumbai, Pune, Chennai, Gurgaon':['Pune','Mumbai','Chennai','Gurugram'],
        'Gurgaon / Gurugram':['Gurugram'], 'New Delhi':['Delhi'],
        'Bombay / Madras / Calcutta':['Mumbai','Chennai','Kolkata'],
        'Cochin / Trivandrum':['Kochi','Thiruvananthapuram'],
        'India':[], 'India Remote':[], 'Remote':[], 'Indiana':[],
        'Indianapolis, Indiana, USA':[], 'London, UK':[],
        'Bangaloreville':[], 'XBLR':[], 'Bengalurux':[]
      };
      for (const [raw,expected] of Object.entries(cases)) {
        assert.deepEqual(Array.from(run(`cityTags(${JSON.stringify(raw)})`)),expected,raw);
      }
    """)


def test_normalize_retains_display_and_prefers_valid_backend_city_tags():
    run_scenario(r"""
      await tick();
      const rows = [
        {location:'Bangalore / Hyderabad - Campus'},
        {location:'Company campus',cities:['Gurgaon','Gurugram','Bengaluru']},
        {location:'Bangalore',cities:[]},
        {location:'BLR',cities:null},
        {location:'BLR',cities:'Bengaluru'},
        {location:'BLR',cities:[null]},
        {location:'BLR',cities:[' ']},
        {location:'Paris, France',cities:['Paris']}
      ];
      run(`jobs=normalize(${JSON.stringify(rows)});tableQuery='';`);
      assert.equal(run('jobs[0].city'),'Bangalore / Hyderabad - Campus');
      assert.deepEqual(JSON.parse(run('JSON.stringify(jobs.map(j=>j.cities))')),
        [['Bengaluru','Hyderabad'],['Gurugram','Bengaluru'],[],['Bengaluru'],
         ['Bengaluru'],['Bengaluru'],['Bengaluru'],['Paris']]);
      assert(run('jobTable(jobs)').includes('Bengaluru + Hyderabad'));
    """)


def test_dropdown_and_counts_merge_aliases_and_filter_multi_city_membership():
    run_scenario(r"""
      await tick();
      run(`jobs=normalize([
        {id:'a',location:'Bangalore'}, {id:'b',location:'Bengaluru'},
        {id:'c',location:'BLR - campus'}, {id:'d',location:'Bangalore / Hyderabad'},
        {id:'e',location:'India'}, {id:'f',location:'Indiana, USA'}
      ]);populateFilters();`);
      assert.equal((el('city-filter').innerHTML.match(/<option>Bengaluru<\/option>/g)||[]).length,1);
      assert(!el('city-filter').innerHTML.includes('Bangalore'));
      assert(!el('city-filter').innerHTML.includes('campus'));
      assert(el('city-filter').innerHTML.includes('All locations (India)'));
      assert.deepEqual(JSON.parse(run("JSON.stringify(counts('city',jobs))")),[['Bengaluru',4],['Hyderabad',1]]);
      el('city-filter').value='Bengaluru';
      assert.deepEqual(JSON.parse(run('JSON.stringify(filtered().map(j=>j.id))')),['a','b','c','d']);
      el('city-filter').value='Hyderabad';
      assert.deepEqual(JSON.parse(run('JSON.stringify(filtered().map(j=>j.id))')),['d']);
      el('city-filter').value='All locations (India)';
      el('role-filter').value='Mid-level';el('experience-filter').value='junior';el('remote-filter').value='Remote';
      navigate('Overview');
      assert.equal(run('filtered().length'),0);
      assert(root.innerHTML.includes('No roles match these filters.'));
    """)


def test_loading_saved_search_canonicalizes_legacy_alias():
    run_scenario(r"""
      await tick();
      const savedButton = new Element('saved');savedButton.dataset.saved='legacy';
      el('saved-workspace').querySelectorAll=()=>[savedButton];
      context.savedRows=[{id:'legacy',data_type:'search',payload:{city:'Bangalore',source:'All sources'}}];
      run(`authSession={user:{id:'user'}};
        authClient={from:()=>({select:()=>({eq:()=>({order:async()=>({data:savedRows})})})})};
        jobs=normalize([{location:'BLR'}]);populateFilters();`);
      await run('loadSaved()');savedButton.emit('click');
      assert.equal(el('city-filter').value,'Bengaluru');
      assert.equal(run('filtered().length'),1);
    """)


def test_actual_table_typing_preserves_cursor_and_dates_sort_chronologically():
    run_scenario(r"""
      await tick();
      Element.prototype.focus=function(){this.focused=true;};
      Element.prototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end;};
      // Re-rendering replaces these controls in a browser. Model that lifecycle
      // so listeners attached to a replacement cannot run in the current event.
      let html=root.innerHTML;
      Object.defineProperty(root,'innerHTML',{
        get:()=>html,set:value=>{html=value;elements.delete('table-search');elements.delete('table-sort');}
      });
      run(`jobs=normalize([
        {title:'Old Python Role',location:'Bangalore',posted:'31 Dec 2025'},
        {title:'New Python Role',location:'BLR',posted:'01 Jan 2026'},
        {title:'Newest SQL Role',location:'Hyderabad',posted:'2026-02-01'}
      ]);`);
      navigate('Overview');
      assert.equal(run('sortDir'),-1);
      assert(root.innerHTML.indexOf('Newest SQL Role')<root.innerHTML.indexOf('New Python Role'));
      assert(root.innerHTML.indexOf('New Python Role')<root.innerHTML.indexOf('Old Python Role'));
      const search=el('table-search');search.value='Python';search.selectionStart=3;
      search.emit('input');
      assert.equal(run('tableQuery'),'Python');
      assert.equal(el('table-search').focused,true);
      assert.equal(el('table-search').selectionStart,3);
      assert(!root.innerHTML.includes('Newest SQL Role'));
      assert(root.innerHTML.includes('Old Python Role'));
      el('table-sort').value='title';el('table-sort').emit('change');
      assert.equal(run('sortDir'),1);
      el('table-sort').value='posted';el('table-sort').emit('change');
      assert.equal(run('sortDir'),-1);
      assert(root.innerHTML.indexOf('New Python Role')<root.innerHTML.indexOf('Old Python Role'));
    """)


def test_salary_view_is_removed_from_public_dashboard():
    run_scenario(r"""
      await tick();
      run(`jobs=normalize([
        {location:'Bangalore, India',salary_min_lpa:10,salary_max_lpa:20,skills:['Python']},
        {location:'Bengaluru; Mumbai',salary_min_lpa:20,salary_max_lpa:30,skills:['Python']},
        {location:'BLR',salary_min_lpa:null,salary_max_lpa:null,skills:['Python']}
      ]);populateFilters();`);
      assert(!run("NAV.some(([name])=>name==='Salaries')"));
      assert(!el('navigation').innerHTML.includes('Salaries'));
      navigate('Overview');
      run('render()');
      assert(!root.innerHTML.toLowerCase().includes('salary'));
      assert(!el('kpis').innerHTML.toLowerCase().includes('salary'));
    """)


def test_years_and_work_mode_are_not_invented_from_titles():
    run_scenario(r"""
      await tick();
      run(`jobs=normalize([
        {id:'explicit',title:'Software Engineer',location:'Bangalore',experience_min:1},
        {id:'unknown',title:'Junior Engineer',location:'Bengaluru'}
      ]);populateFilters();`);
      el('experience-filter').value='junior';
      assert.deepEqual(JSON.parse(run('JSON.stringify(filtered().map(j=>j.id))')),['explicit']);
      assert.equal(run('jobs[1].remote'),'Unknown');
      assert.equal(run('experience(jobs[1])'),'unknown');
    """)

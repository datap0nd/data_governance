/* Fictional, isolated UI review. This page never calls the application API. */
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const fixtures = {
    multiple: {filename:'regional_orders.xlsx', sheets:[{name:'North',columns:15,rows:120},{name:'South',columns:15,rows:40},{name:'Totals',columns:3,rows:17}]},
    single: {filename:'daily_orders.xlsx', sheets:[{name:'Orders',columns:15,rows:12}]},
    missing: {filename:'regional_orders.xlsx', sheets:[{name:'North 2027',columns:15,rows:120},{name:'South',columns:15,rows:40},{name:'Totals',columns:3,rows:17}]},
    invalid: {filename:'daily_orders.xlsx', sheets:[{name:'Orders',columns:15,rows:12}],invalid:true},
  };
  let saved = {enabled:false, mode:'append', names:[], name:'Regional orders'};
  let observed = null;
  const mode = () => document.querySelector('[name="worksheet-mode"]:checked').value;
  const names = () => $('worksheet-names').value.split(/\r?\n/).filter(name => name.trim().length > 0);
  function show(view) {$('settings-view').hidden=view!=='settings';$('run-view').hidden=view!=='run';$('page-title').textContent=view==='settings'?'Edit flow':'Run details';}
  function openAfter() {document.querySelectorAll('[data-section]').forEach(button=>{const active=button.dataset.section==='after';button.setAttribute('aria-expanded',String(active));$(button.dataset.section).hidden=!active;});}
  function drawDetected() {
    $('detected').hidden=!observed;
    if (!observed) return;
    $('detected-file').textContent=observed.filename;
    $('detected-buttons').innerHTML=observed.sheets.map(sheet=>`<button type="button" class="btn-secondary worksheet-chip" data-sheet="${esc(sheet.name)}" aria-pressed="${names().includes(sheet.name)}">${esc(sheet.name)}</button>`).join('');
    $('detected-buttons').querySelectorAll('button').forEach(button=>button.onclick=()=>{const selected=names(),name=button.dataset.sheet;$('worksheet-names').value=(mode()==='one'?[name]:selected.includes(name)?selected.filter(item=>item!==name):[...selected,name]).join('\n');$('selection-error').textContent='';drawDetected();});
  }
  function update() {$('named-fields').hidden=!$('worksheet-enabled').checked;const one=mode()==='one';$('names-label').textContent=one?'Worksheet name':'Worksheet names';$('names-help').textContent=one?'Use the exact name, including spaces and case.':'One exact name per line, in the order to append.';$('worksheet-names').rows=one?1:4;$('worksheet-names').placeholder=one?'North':'North\nSouth';$('selection-help').textContent=one?'Only the named worksheet is loaded. Your SQL write mode stays the same.':'The named worksheets must have matching columns. All their data rows are included. Your SQL write mode stays the same.';$('selection-error').textContent='';drawDetected();}
  function restore() {$('worksheet-enabled').checked=saved.enabled;document.querySelector(`[name="worksheet-mode"][value="${saved.mode}"]`).checked=true;$('worksheet-names').value=saved.names.join('\n');$('flow-name').value=saved.name;update();}
  $('worksheet-enabled').onchange=update;
  document.querySelectorAll('[name="worksheet-mode"]').forEach(input=>input.onchange=update);
  $('worksheet-names').oninput=()=>{$('selection-error').textContent='';drawDetected();};
  document.querySelectorAll('[data-section]').forEach(button=>button.onclick=()=>{const expanded=button.getAttribute('aria-expanded')==='true';document.querySelectorAll('[data-section]').forEach(item=>{const active=item===button&&!expanded;item.setAttribute('aria-expanded',String(active));$(item.dataset.section).hidden=!active;});});
  $('show-settings').onclick=()=>show('settings');$('show-run').onclick=()=>show('run');
  $('cancel-edit').onclick=()=>{restore();$('save-feedback').textContent='';show('run');};
  $('worksheet-form').onsubmit=event=>{event.preventDefault();const selected=names();$('save-feedback').textContent='';
    if($('worksheet-enabled').checked&&(!selected.length||new Set(selected).size!==selected.length||mode()==='one'&&selected.length!==1||mode()==='append'&&selected.length<2)){$('selection-error').textContent=!selected.length?'Enter the exact worksheet name'+(mode()==='append'?'s.':'.'):new Set(selected).size!==selected.length?'Each worksheet name must appear only once.':mode()==='one'?'Enter exactly one worksheet name.':'Enter at least two worksheet names to append, or choose Load one named worksheet.';openAfter();$('worksheet-names').focus();return;}
    if($('fail-save').checked){$('save-feedback').dataset.state='error';$('save-feedback').textContent='Could not save the flow. Your worksheet selection is still here. Try saving again.';return;}
    saved={enabled:$('worksheet-enabled').checked,mode:mode(),names:$('worksheet-enabled').checked?selected:[],name:$('flow-name').value};$('save-feedback').dataset.state='success';$('save-feedback').textContent='Flow saved. These worksheet settings apply to future runs.';
  };
  function run() {
    observed=fixtures[$('scenario').value];drawDetected();const available=observed.sheets.map(sheet=>sheet.name),list=available.map(name=>`“${name}”`).join(', ');let selected=[];let message='';let reason='';
    if(!saved.enabled&&available.length!==1){reason='This Excel has more than one sheet. Please enable the option in Flows.';message=`${observed.filename} contains ${available.length} worksheets: ${list}. Enable “Choose how to load Excel worksheets” in this flow’s After download settings.`;}
    else if(saved.enabled&&saved.names.some(name=>!available.includes(name))){const absent=saved.names.filter(name=>!available.includes(name));reason='A selected worksheet is missing';message=`${observed.filename}: selected worksheet ${absent.map(name=>`“${name}”`).join(', ')} was not found. Available worksheets: ${list}. Check the saved names.`;}
    else {selected=!saved.enabled?observed.sheets:saved.names.map(name=>observed.sheets.find(sheet=>sheet.name===name));if(saved.enabled&&saved.mode==='append'&&selected.some(sheet=>sheet.columns!==selected[0].columns)){reason='The selected worksheets have incompatible columns';message=`${observed.filename}: “${selected[0].name}” has ${selected[0].columns} columns; “${selected.find(sheet=>sheet.columns!==selected[0].columns).name}” has ${selected.find(sheet=>sheet.columns!==selected[0].columns).columns}. These worksheets cannot be appended. No rows were discarded.`;}else if(observed.invalid){reason='Excel processing failed';message=`${observed.filename}, worksheet “Orders”, row 9: found 16 populated columns but the detected header has 15. No cells were discarded. Check this row and its header.`;}}
    const rows=selected.reduce((count,sheet)=>count+sheet.rows,0);
    const events=[['08:00:00','Download completed',`${observed.filename} saved.`],['08:00:01','Workbook inspected',`${available.length} worksheets found: ${list}.`]];
    if(message){events.push(['08:00:01','Excel processing failed',message],['—','SQL not started','Workbook processing did not complete.']);$('run-status').textContent='Failed · Excel processing';$('run-result').innerHTML=`<div class="worksheet-failure"><h3>${esc(reason)}</h3><p>${esc(message)}</p><p><strong>SQL did not start. The original workbook is preserved.</strong></p><button type="button" class="btn-secondary" id="fix-selection">${observed.invalid?'View worksheet settings':'Choose worksheets'}</button></div>`;}
    else {events.push(['08:00:02','Worksheets processed',selected.map(sheet=>`${sheet.name}: ${sheet.rows} data rows, ${sheet.columns} columns`).join('; ')],['08:00:03','SQL committed',`${rows} rows loaded from ${selected.length} selected worksheet(s).`]);$('run-status').textContent='Succeeded';$('run-result').innerHTML=`<div class="worksheet-success"><h3>Workbook processed successfully</h3><p>${rows} rows loaded from ${selected.map(sheet=>esc(sheet.name)).join(', ')}.</p><p>Original workbook preserved. Worksheet selection and row counts are recorded below.</p></div>`;}
    $('run-result').innerHTML+=`<div class="worksheet-table-wrap"><table class="worksheet-events"><thead><tr><th>Time</th><th>Stage</th><th>What happened</th></tr></thead><tbody>${events.map(event=>`<tr>${event.map(value=>`<td>${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    $('fix-selection')?.addEventListener('click',()=>{show('settings');openAfter();$('worksheet-enabled').checked=true;update();$('worksheet-names').focus();});
  }
  $('simulate-run').onclick=run;
  $('reset-preview').onclick=()=>{saved={enabled:false,mode:'append',names:[],name:'Regional orders'};observed=null;$('scenario').value='multiple';$('fail-save').checked=false;restore();$('save-feedback').textContent='';$('run-status').textContent='Ready to simulate';$('run-result').innerHTML='<p class="worksheet-help">Run the fictional workbook through the saved worksheet setting.</p>';show('settings');openAfter();};
  update();show('settings');
})();

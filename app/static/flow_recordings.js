/* Flow setup belongs to Edit Flow; the recorder only edits actions. */
window.FlowRecordings = (() => {
    function create() {
        const sites = window._flowsState?.catalog?.sites || [];
        const site = sites.find(item => item.enabled && ['asap_portal', 'gscm_portal'].includes(item.adapter));
        if (!site) { toast('Add an ASAP or GSCM website in Catalog first.'); return; }
        _flowShowView('builder', {site_id: site.id, execution_method: 'recorded'});
    }
    const open = (flowId, settings=null) => {
        if (settings) window._flowBuilderDrafts?.set(flowId, {...settings});
        return window.RecordedFlowEditor.open(flowId, settings);
    };
    return {create, open};
})();

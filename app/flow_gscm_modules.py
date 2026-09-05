"""Configured Favorite module selection through Nexacro Combo controls.

Read the bound option dataset, then use real keyboard selection so Nexacro
raises its normal onitemchanged event. set_value alone does not raise it.
"""
from __future__ import annotations

import json

MODULE_JS = r"""({suffix, open}) => {
    if (typeof nexacro === 'undefined' || !nexacro.getApplication) return [];
    const app = nexacro.getApplication(), result = [];
    for (const element of document.querySelectorAll('[id]')) {
        const id = element.id;
        if (!/\.Setting\d+\.form\./.test(id) || !/\.(?:cbo|cmb|Combo)[A-Za-z0-9_]*$/.test(id)) continue;
        if (suffix && !id.endsWith(suffix)) continue;
        const box=element.getBoundingClientRect();
        if (!box.width || !box.height || getComputedStyle(element).visibility==='hidden') continue;
        const parts=id.split('.');
        if (parts.some(part=>!/^\w+$/.test(part) || ['__proto__','prototype','constructor'].includes(part))) continue;
        let control=app;
        for(const part of parts) control=control?.[part];
        if (!control || typeof control.getInnerDataset !== 'function') continue;
        const dataset=control.getInnerDataset();
        if (!dataset || !control.codecolumn || !control.datacolumn) continue;
        const options=[];
        for(let row=0; row<dataset.getRowCount(); row++) options.push({
            value:String(dataset.getColumn(row,control.codecolumn)),
            label:String(dataset.getColumn(row,control.datacolumn))});
        result.push({id, suffix:id.replace(/^.*\.Setting\d+/,''), value:String(control.value), options});
        if (open && id === open && typeof control.dropdown === 'function') control.dropdown();
    }
    return result;
}"""


def inventory(page, suffix=None):
    from app.flow_gscm import _roots
    found=[]
    for root in _roots(page):
        try:
            records=root.evaluate(MODULE_JS, {'suffix':suffix,'open':None})
        except Exception:
            continue
        if isinstance(records,list):
            found.extend((root,item) for item in records if isinstance(item,dict))
    return found


def select(page, value, suffix):
    matches=inventory(page,suffix)
    if len(matches)!=1:
        raise RuntimeError('GSCM module binding is unavailable or ambiguous. Configure its exact Favorite Combo suffix.')
    root,control=matches[0]
    indices=[index for index,option in enumerate(control['options']) if option['value']==value]
    if len(indices)!=1 or len(control['options'])>50:
        raise RuntimeError('The configured GSCM module is missing or ambiguous.')
    if control['value'] != value:
        node=root.locator('[id='+json.dumps(control['id'])+']')
        node.click()
        root.evaluate(MODULE_JS, {'suffix':suffix,'open':control['id']})
        node.press('Home')
        for _ in range(indices[0]):
            node.press('ArrowDown')
        node.press('Enter')
        after=inventory(page,suffix)
        if len(after)!=1 or after[0][1]['value'] != value:
            raise RuntimeError('GSCM rejected the module selection.')
    return control

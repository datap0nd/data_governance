import pytest
from playwright.sync_api import sync_playwright
from app import flow_recording, flow_recording_runtime
from test_flow_recordings import definition


@pytest.mark.parametrize('title',['GSCM','ASAP','MicroStrategy','Excel down','Download'])
def test_generic_portal_and_download_names_are_not_report_identity(title):
    value=definition();value['identity']['text']=title
    with pytest.raises(ValueError,match='report-specific'):
        flow_recording.validate_definition(value)


def test_button_text_cannot_establish_report_identity_even_through_a_span():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page();page.set_content('<button><span>Sales Report</span></button>')
        value={'identity':{'text':'Sales Report','target':{'page':'page','locator':[{'method':'get_by_text','args':['Sales Report'],'kwargs':{'exact':True}}]}}}
        with pytest.raises(RuntimeError,match='button'):
            flow_recording_runtime._identity({'page':page},value)
        page.set_content('<h1>Sales Report</h1>')
        flow_recording_runtime._identity({'page':page},value)
        browser.close()

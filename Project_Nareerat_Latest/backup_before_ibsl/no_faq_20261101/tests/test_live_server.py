import urllib.request
import json

import urllib.request
import json

def test_live_server_endpoints():
    # 1. Test index page HTML layout
    req = urllib.request.urlopen('http://127.0.0.1:8000/')
    html = req.read().decode('utf-8')
    print('1. Index page status: 200 OK')
    
    assert 'search-query-display' in html, "Search query indicator must exist"
    assert 'retrieved-fields-list' in html, "Retrieved fields list container must exist"
    assert 'retrieved-faqs-list' not in html, "Separate FAQ list section was removed (FAQ answers live inside field cards)"
    assert 'quickFillSearch' not in html, "Suggested keywords under the search box were removed"
    assert 'search-metric-precision' in html, "Precision/Recall/F1 strip must be under the search box"
    assert 'synthesized-answer-text' in html, "Synthesized answer text container must exist"
    
    fields_idx = html.find('retrieved-fields-list')
    ans_idx = html.find('synthesized-answer-text')

    # Overall AI answer at the VERY BOTTOM of search results
    assert fields_idx < ans_idx, "Overall AI answer must be placed at the bottom below fields"
    print(f'   -> Verified Order: Fields (idx {fields_idx}) -> Overall Answer at bottom (idx {ans_idx}).')

    # 2. Test status API
    req_status = urllib.request.urlopen('http://127.0.0.1:8000/api/status')
    status_data = json.loads(req_status.read().decode('utf-8'))
    print('2. Status API:', status_data)
    assert status_data['field_count'] == 209, "Field count must be 209"
    assert status_data['faq_count'] == 36, "FAQ count must be 36"

    # 3. Test search API with pol_status
    search_payload = json.dumps({'query': 'pol_status'}).encode('utf-8')
    req_search = urllib.request.Request(
        'http://127.0.0.1:8000/api/search',
        data=search_payload,
        headers={'Content-Type': 'application/json'}
    )
    resp_search = json.loads(urllib.request.urlopen(req_search).read().decode('utf-8'))
    print('3. Search API: query =', resp_search['query'])
    assert 'answer' in resp_search and len(resp_search['answer']) > 20, "Answer must be generated"
    print('   Answer snippet:', resp_search['answer'][:120])
    
    fields = resp_search['fields']
    faqs = resp_search['faqs']
    items = resp_search['items']
    print(f'   Fields count: {len(fields)}, FAQs count: {len(faqs)}, Total items: {len(items)}')
    
    assert len(fields) > 0, "Must return matching fields"
    assert items == fields, "Displayed/saved items are fields only"
    assert any(f.get('faq_answers') for f in fields), "FAQ answers must be embedded in field cards"
    
    print('\nAll live server checks passed successfully!')

if __name__ == '__main__':
    test_live_server_endpoints()

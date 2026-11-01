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
    assert status_data['knowledge_scope'] == 'policy', "Only policy data is in the knowledge base"
    assert status_data['topic_count'] == 24, "IBSL policy categories 1.1-1.6 give 24 topics"
    assert status_data['field_count'] == 209, "Policy table + 13 sub-tables give 209 fields"
    assert 'faq_count' not in status_data, "FAQ.xlsx is no longer a knowledge source"

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
    
    items = resp_search['items']
    print(f'   Items count: {len(items)}')

    assert len(items) > 0, "Must return matching fields"
    assert 'faqs' not in resp_search, "FAQ.xlsx is no longer a knowledge source"
    top = items[0]
    assert top['field_name'] == 'pol_status' and top['has_ibsl'], "pol_status must link to its IBSL topic"
    assert top['primary_ibsl']['section_no'] == '1.1.5', "Primary topic must be the field's own topic"
    assert top['validation_tables'] and top['validation_tables'][0]['test_cases'], "Structured example table must be served"
    
    print('\nAll live server checks passed successfully!')

if __name__ == '__main__':
    test_live_server_endpoints()

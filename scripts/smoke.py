"""Run the required end-to-end API path against an already running local server."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    args = parser.parse_args()

    def call(path, payload=None):
        request = Request(
            args.url + '/api' + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(error.read().decode()) from error

    started = time.perf_counter()
    call('/recalculate', {})
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = call('/recalculate/status')
        if status['status'] == 'failed':
            raise RuntimeError(status)
        if status['status'] == 'completed':
            break
        time.sleep(0.25)
    else:
        raise TimeoutError('Recalculation exceeded five minutes')
    top = call('/top-nodes?limit=20')
    assert len(top) >= 20, 'Expected at least 20 nodes on the supplied dataset'
    gid = top[0]['gid']
    node = call(f'/nodes/{gid}')
    assert node['evidence'] and 0 <= node['priority_score'] <= 1
    graph = call(f'/nodes/{gid}/ego?depth=1&direction=both')
    assert any(item['gid'] == gid for item in graph['nodes'])
    answer = call('/copilot', {'message': f'Почему gid {gid} находится в топе?'})
    assert answer['tools_used'] and str(gid) in answer['answer']
    assert any(link['id'] == gid for link in answer['links'])
    print(json.dumps({'status': 'PASS', 'gid': gid, 'top_count': len(top),
                      'ego_nodes': len(graph['nodes']), 'copilot_mode': answer['mode'],
                      'seconds': round(time.perf_counter() - started, 3)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Combine independent launches without dropping provenance or failed candidates."""

def combine(reports, artifacts, fingerprints=False):
    if not 1 <= len(reports) <= 8 or not artifacts:
        raise ValueError('measurement process count')
    accepted = {a['measurement'] for a in artifacts}
    baseline = artifacts[0]['measurement']
    for report in reports:
        accepted &= {row['measurement'] for row in report['rows']}
    if baseline not in accepted:
        raise ValueError('baseline failed fresh process validation')
    result = dict(schema=1, processes=len(reports),
        rows=[dict(row, process=index) for index, report in enumerate(reports)
              for row in report['rows'] if row['measurement'] in accepted],
        rejected=[dict(row, process=index) for index, report in enumerate(reports)
                  for row in report['rejected']],
        module_load=[dict(row, process=index) for index, report in enumerate(reports)
                     for row in report['module_load']])
    if fingerprints:
        inputs = reports[0]['input_fingerprints']
        if any(report['input_fingerprints'] != inputs for report in reports):
            raise ValueError('measurement normalized inputs changed across processes')
        result['input_fingerprints'] = inputs
    return result

from fastapi import HTTPException

from .database import utcnow
from .inventory.local_sync import LocalSyncInventoryProvider
from .notifications import queue_email


def fulfil_request(db, provider, mapping, request_id, status, user):
    request = db.execute('SELECT * FROM item_requests WHERE id=?', (request_id,)).fetchone()
    if not request: raise HTTPException(404, 'Request not found')
    if request['status'] in ('complete', 'closed'):
        raise HTTPException(409, 'This historical request is already complete')
    now = utcnow()
    linked_id = request['inventory_item_id']
    if linked_id and status == 'ordered':
        raise HTTPException(409, 'This request has already been received')
    if status == 'available' and not linked_id:
        fields = {mapping['name']: request['item_requested'], mapping['stock']: request['quantity']}
        columns = set(provider.get_columns())
        if mapping.get('model') in columns: fields[mapping['model']] = request['manufacturer_model']
        if mapping.get('manufacturer') in columns: fields[mapping['manufacturer']] = request['manufacturer']
        if mapping.get('description') in columns: fields[mapping['description']] = request['item_requested']
        if isinstance(provider, LocalSyncInventoryProvider):
            item = provider.add_item_in_transaction(db, fields, mapping)
        else:
            item = provider.add_item(fields)
        linked_id = item['id']
        db.execute("INSERT INTO transactions(created_at,user_id,username,item_id,item_name,quantity,old_soh,new_soh,transaction_type,success,sync_status,sync_operation_id) VALUES(?,?,?,?,?,?,0,?,'request_received',1,?,?)",
                   (now, user['id'], user['username'], item['id'], item['name'], item['stock'], item['stock'], item.get('sync_status','synced'), item.get('sync_operation_id')))
    db.execute('UPDATE item_requests SET status=?,inventory_item_id=?,updated_at=? WHERE id=?', (status, linked_id, now, request_id))
    if status == 'available' and request['status'] != 'available' and request['notify_available']:
        queue_email(db, f'request:{request_id}:available', 'user', 'Your requested item is available',
                    {'Request number': request_id, 'Item': request['item_requested'], 'Quantity requested': request['quantity'], 'Status': 'Available — contact your inventory administrator for collection'}, request['requested_by'])
    return {'id': request_id, 'status': status, 'inventory_item_id': linked_id}

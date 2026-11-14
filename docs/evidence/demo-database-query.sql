-- Read-only query of the retained Demo schema: ntou_acceptance_demo_20261114_b8877ae024
SELECT o.buyer_id, o.timestamp, o.order_id,
       o.meal_id, o.order_state, d.deliverer_id
FROM orders AS o
JOIN delivery AS d
  ON o.buyer_id = d.buyer_id AND o.timestamp = d.timestamp
WHERE (o.buyer_id = 1 AND o.timestamp = '2030-01-18 20:40:51')
   OR (o.buyer_id = 4 AND o.timestamp = '2030-02-06 23:42:18')
ORDER BY o.buyer_id, o.order_id;

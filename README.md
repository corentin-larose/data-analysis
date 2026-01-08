source venv/bin/activate
pip install --upgrade pip setuptools wheel
brew install libmagic
pip install opennsfw2 pillow python-magic tqdm



Competr tous les fichiers
find data/attachments -type f | wc -l

Compter les JPG
find data/attachments -type f -name "*.jpg" | wc -l

Espace disque occupé
du -sh data/attachments

nohup ./venv-nsfw/bin/python nsfw.py data/attachments \
--output found \[scan_log.csv](found/scan_log.csv)
--threshold 0.75 \
--workers 6 >  nsfw.log 2>&1 &

curl -X DELETE "localhost:9200/emails"    
curl -X PUT "http://localhost:9200/emails" -H 'Content-Type: application/json' --data-binary "@docs/ElasticsearchEmailMapping.json"
{"acknowledged":true,"shards_acknowledged":true,"index":"emails"}%    

http://localhost:9200/emails/_search?pretty


Totla des échanges
MATCH (s:Person)-[summary:TOTAL_EXCHANGES]->(t:Person)
RETURN s, summary, t
ORDER BY summary.count DESC
LIMIT 25

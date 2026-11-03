"""CPU behavior admission from raw source wording; no annotation files are read."""
import json
from pathlib import Path
from threading import Lock
import time
import torch
from transformers import AutoTokenizer,BertForSequenceClassification

MODEL_PATH=Path(__file__).resolve().parents[3]/'llm/bert-behavior-admission-v4'


def record_texts(record):
    texts=[]
    for r in record['records']:
        cabin=r['device_tree'].get('cabin',{})
        hmi=cabin.get('screen',{}).get('hmi',{})
        trigger=r.get('trigger') or {}
        source={'detail':trigger.get('detail',''),'query':cabin.get('dialogue',{}).get('query',''),
            'hmi_description':hmi.get('description',''),'last_touch':hmi.get('last_touch',''),
            'control_name':hmi.get('last_control_name','')}
        if r['type']!='trigger':source['content']=json.dumps(r.get(r['type'],{}),ensure_ascii=False)
        text='\n'.join(f'{key}: {source.get(key) or ""}' for key in
            ['detail','query','hmi_description','last_touch','control_name','content'] if source.get(key))
        if text:texts.append(text)
    return texts or ['无来源文字']


class BehaviorAdmission:
    def __init__(self,path=MODEL_PATH):
        torch.set_num_threads(4)
        self.tokenizer=AutoTokenizer.from_pretrained(path,use_fast=False,local_files_only=True)
        self.model=BertForSequenceClassification.from_pretrained(path,local_files_only=True).eval()
        self.lock=Lock()

    def predict(self,record):
        start=time.monotonic()
        texts=record_texts(record)
        lengths=[len(self.tokenizer.encode(t,add_special_tokens=True,truncation=False)) for t in texts]
        if max(lengths)>512:raise ValueError(f'{record["session_id"]} source exceeds 512 tokens: {lengths}')
        batch=self.tokenizer(texts,padding=True,return_tensors='pt',truncation=False)
        with self.lock,torch.inference_mode():
            logits=self.model(**batch).logits
            probabilities=torch.sigmoid(logits[:,1]-logits[:,0]).tolist()
        score=max(probabilities)
        return {'probability_nonempty':score,'record_probabilities':probabilities,'nonempty':score>=.5,
            'threshold':.5,'record_token_lengths':lengths,'seconds':time.monotonic()-start,'device':'cpu'}

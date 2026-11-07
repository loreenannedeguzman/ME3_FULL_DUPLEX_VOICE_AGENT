"""Bounded read-back of completed owned calculator results, never a tool call."""
import json,re
from decimal import Decimal,InvalidOperation

SMALL='zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split()

def verbatim_passage(registry,alias,args,data,completed):
    """A model-selected exact passage, not an intent phrase or arbitrary tool text.

    Only the owned library contract may bypass a rewriting round. A prior
    unrelated read or a mixed tool batch still needs the ordinary reasoning loop.
    No tool call, action, cursor advancement or extra request occurs here.
    """
    if args.get('delivery')!='verbatim':return None
    specs={s['id']:s for s in registry.config['servers']}
    def owned(key,allowed):
        origin=registry.reads.get(key);spec=specs.get(origin[0],{}) if origin else {}
        return bool(origin and origin[1] in allowed and spec.get('module')=='me3_assistant.library_server' and spec.get('identity')=='me3')
    if not owned(alias,{'read_document'}):return None
    current=json.dumps([alias,args],sort_keys=True)
    for key in completed:
        old_alias,_=json.loads(key)
        if key!=current and not owned(old_alias,{'list_documents','search_documents'}):return None
    limit=args.get('max_words');text=data.get('text') if isinstance(data,dict) else None
    if (not isinstance(data,dict) or data.get('ok') is not True or data.get('read_only') is not True
        or data.get('delivery')!='verbatim' or data.get('max_words')!=limit
        or not isinstance(args.get('document_id'),str) or data.get('document_id')!=args['document_id'] or data.get('offset')!=args.get('offset',0)
        or not isinstance(data.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',data['sha256'])
        or not isinstance(text,str) or len(text)>480 or any(ord(c)<32 and c not in '\n\t' for c in text)
        or limit is not None and (type(limit) is not int or not 1<=limit<=80 or len(text.split())>limit)):
        return None
    if args['document_id'].lower().endswith('.pdf') and data.get('page')!=args.get('page',1):return None
    if args.get('expected_sha256') is not None and data['sha256']!=args['expected_sha256']:return None
    if not text.strip():
        return ('This page has no extractable text. I cannot read scanned text without OCR.'
                if data.get('page_has_extractable_text') is False else 'There is no text in this passage to read aloud.')
    return text.strip()

def stated_result(speech,result):
    # A number in the INPUT expression is not evidence that the result was said.
    values=[re.escape(result)]
    value=Decimal(result)
    if value==value.to_integral() and abs(value)<=20:
        word=SMALL[abs(int(value))]
        values.append(('(?:minus|negative)\\s+' if value<0 else '')+word)
    term='(?:'+'|'.join(values)+')'
    return bool(re.fullmatch(r'\s*'+term+r'[.!]?\s*',speech,re.I) or
                re.search(r'(?:\b(?:equals?|equal to|is|result(?: is)?|answer(?: is)?)\s+|=\s*)'+term+r'(?![\w.])',speech.rstrip('.!?'),re.I))


def non_math_sentences(speech):
    """Remove only entirely arithmetic fragments when replacing missing values.

    Tool choice does not describe all user intent. Keep other prose rather than
    treating a calculator-only tool trace as a calculator-only conversation.
    This lexical filter is not a general evaluator of model factual accuracy.
    """
    words=set(SMALL)|set('thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion point plus minus times multiplied divided by over percent percentage squared cubed power negative positive equals equal to is are was be the a an i we you have has calculated calculate calculation calculations calculator expression expressions result results answer answers as for of and or it this that also did done other'.split())
    kept=[]
    for sentence in re.split(r'(?<=[.!?;])\s+',speech.strip()):
        tokens=re.findall(r'[A-Za-z]+|\d+(?:\.\d+)?|[^\sA-Za-z0-9]',sentence)
        if tokens and all(t.casefold() in words or re.fullmatch(r'\d+(?:\.\d+)?',t) or t in '+-*/=.,!?;:()%×÷' for t in tokens):continue
        kept.append(sentence)
    return ' '.join(kept)

def deliver_results(registry,completed,speech):
    """A fallback for omitted calculator values, not a general truth evaluator.

    Only the configured student-owned utility's successful result contract is
    eligible. Third-party tools with similar names remain ordinary model data.
    Mixed requests retain their other answers; no RPC, model or TTS is invoked.
    """
    facts=[];all_facts=[]
    specs={s['id']:s for s in registry.config['servers']}
    for key,data in completed.items():
        alias,args=json.loads(key);origin=registry.reads.get(alias)
        spec=specs.get(origin[0],{}) if origin else {}
        owned=(origin and origin[1]=='calculate_expression' and
               spec.get('module')=='me3_assistant.utility_server' and spec.get('identity')=='me3')
        if not owned:continue
        if (not isinstance(data,dict) or data.get('ok') is not True or data.get('read_only') is not True or
            data.get('expression')!=args.get('expression') or not isinstance(data.get('result'),str)):
            continue
        result=data['result'];expression=data['expression']
        try:
            value=Decimal(result)
            if not value.is_finite() or abs(value)>Decimal('1e15'):continue
        except InvalidOperation:continue
        if not re.fullmatch(r'-?\d+(?:\.\d+)?',result):continue
        fact=f'For {expression}, the calculator result is {result}.'
        all_facts.append(fact)
        if not stated_result(speech,result):facts.append(fact)
    if not facts:return speech,0
    # Facts take priority; never clip a numeric value to fit the speech bound.
    # Repeat verified values if needed, but retain non-arithmetic sentences even
    # when every executed tool was a calculator. Those sentences may answer a
    # separate conversational part of the same request.
    prefix=' '.join(all_facts)
    if len(prefix)>650:
        return 'The calculations completed, but their full results exceed one spoken reply. Please request fewer results at a time.',len(facts)
    speech=non_math_sentences(speech)
    if not speech:return prefix,len(facts)
    if len(prefix)+1+len(speech)<=650:return prefix+' '+speech,len(facts)
    return prefix+' Other requested details do not fit this reply. Please ask for those separately.' if len(prefix)<=555 else prefix,len(facts)

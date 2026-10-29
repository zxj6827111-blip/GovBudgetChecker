"""年度模板维护：数据库元数据、受管原件、不可变发布与年度修订。"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4, UUID
from fastapi import HTTPException
from src.services.disclosure_template_import import import_source, compare_text

ALGORITHM = 'material-writing-v3.6'
CAPABILITIES = {'writing_auto', 'position_checks', 'residue', 'structure'}
WRITING_CAPABILITIES = ('writing_auto', 'position_checks')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def row(value):
    if value is None: return None
    data = dict(value)
    for key, val in list(data.items()):
        if key in {'content','capability_scope','review_record','validation_record','data','before_state','after_state'} and isinstance(val, str):
            data[key] = json.loads(val)
        elif isinstance(val, UUID) or hasattr(val, 'isoformat'): data[key] = str(val)
    return data


def storage():
    return Path(os.environ.get('GBC_TEMPLATE_STORAGE') or str(Path(os.environ.get('UPLOAD_DIR','uploads')) / '.templates')).resolve()


def source_dir(source_id):
    return storage() / str(UUID(str(source_id)))


def config_2024():
    # 固定算法只启用已审核位置，上传内容不能注入任意可执行正则或金额公式。
    return {'schema': 2, 'algorithm': ALGORITHM, 'd2': 'low',
            'markers': ['part_heading', 'table_note'],
            'native': {'section':'预算绩效管理', 'sentence':'本部门建立了如下预算绩效管理制度'}}


async def audit(conn, action, actor, reason, before=None, after=None):
    await conn.execute('INSERT INTO disclosure_template_audit(id,action,actor,reason,before_state,after_state) VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb)',
                       uuid4(), action, actor, reason, canonical(before), canonical(after))


async def register_source(conn, *, path, metadata, actor):
    source_id = uuid4()
    import asyncio
    manifest = await asyncio.to_thread(import_source, Path(path), source_dir(source_id), metadata)
    duplicates=[str(x['id']) for x in await conn.fetch('SELECT id FROM disclosure_template_sources WHERE source_sha256=$1',manifest['sha256'])]
    async with conn.transaction():
        await conn.execute('''INSERT INTO disclosure_template_sources(id,source_sha256,storage_key,report_kind,material_type,source_report_year,issuer,issue_date,source_description,extraction_status,converter,extraction_error,actor)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8::text::date,$9,$10,$11,$12,$13)''', source_id, manifest['sha256'],str(source_id),
            metadata['report_kind'],metadata['material_type'],metadata['source_report_year'],metadata['issuer'],metadata.get('issue_date') or None,
            metadata['description'],manifest['extraction_status'],manifest['converter'],manifest['error'],actor)
        await audit(conn,'import',actor,metadata['description'],after={'source_id':str(source_id),'manifest':manifest})
    return {'id':str(source_id), 'duplicate_source_ids':duplicates, **manifest}


async def source(conn, source_id):
    data = row(await conn.fetchrow('SELECT * FROM disclosure_template_sources WHERE id=$1',UUID(str(source_id))))
    if not data: raise HTTPException(404,'模板来源不存在')
    directory = source_dir(source_id)
    manifest = json.loads((directory/'manifest.json').read_text())
    original = directory / manifest['original']
    if hashlib.sha256(original.read_bytes()).hexdigest() != data['source_sha256']:
        raise HTTPException(409,'模板原件校验失败')
    return data, manifest, directory


async def text(conn, source_id):
    data, manifest, directory = await source(conn, source_id)
    if data['extraction_status']=='failed': raise HTTPException(409,'模板转换失败，不能继续审核')
    extracted = (directory/'extracted.txt').read_text(encoding='utf-8')
    if hashlib.sha256(extracted.encode()).hexdigest()!=manifest.get('text_sha256'): raise HTTPException(409,'提取文本校验失败')
    return extracted


async def create_draft(conn, data, actor):
    unit,_,_ = await source(conn,data['unit_source_id']); dept,_,_=await source(conn,data['department_source_id'])
    if unit['material_type']!='unit' or dept['material_type']!='department' or unit['report_kind']!=dept['report_kind'] or unit['source_report_year']!=dept['source_report_year']:
        raise HTTPException(422,'部门和单位来源类型、文种或源年度不匹配')
    dept_text=await text(conn,dept['id']); unit_text=await text(conn,unit['id'])
    source_receipts={kind:source_receipt(data['id']) for kind,data in [('unit',unit),('department',dept)]}
    pair_diff=compare_text(dept_text,unit_text)
    config=config_2024()
    config['domain']=unit['report_kind']
    import re
    flat_dept=''.join(dept_text.split()); flat_unit=''.join(unit_text.split())
    config['supported_pair']=all(token in value for token,value in (
        ('年度部门决算',flat_dept),('本表反映部门本年度',flat_dept),
        ('年度决算',flat_unit),('本表反映单位本年度',flat_unit)))
    if unit['report_kind']=='budget':
        config['markers']=['budget_financial_table','budget_function_table']
        config['supported_pair']=all(re.search(pattern,view) for pattern,view in (
            (r'部门财务收支预算总表',flat_dept),(r'部门一般公共预算支出功能分类预算表',flat_dept),
            (r'(?:预算单位|单位)财务收支预算总表',flat_unit),(r'(?:预算单位|单位)一般公共预算支出功能分类预算表',flat_unit)))
    else:
        config['supported_pair']=bool(config['supported_pair'] or (re.search('年度(?:单位)?决算',flat_unit) and '本表反映单位本年度' in flat_unit and '年度部门决算' in flat_dept and '本表反映部门本年度' in flat_dept))
    config['native'] = config['native'] if unit['report_kind']=='final' and config['native']['sentence'] in flat_unit else {}
    unit_manifest=json.loads((source_dir(unit['id'])/'manifest.json').read_text())
    dept_manifest=json.loads((source_dir(dept['id'])/'manifest.json').read_text())
    unit_release=str(unit_manifest['metadata'].get('release_reference') or '').strip()
    dept_release=str(dept_manifest['metadata'].get('release_reference') or '').strip()
    source_pair_consistent=bool(len(unit_release)>=3 and unit_release==dept_release and unit['issuer']==dept['issuer'] and not any(word in unit['issuer'] for word in ('待核','未知','未识别')))

    patterns=[r'无[^。:：]{1,80}的[，,]仍保留该表格式[，,]并在本表下方作下述说明[:：]',
        r'列明公务接待批次[、,，]人次',r'按上述格式依次说明到各项级科目',r'[（(]如持平作持平表述[）)]',
        r'列明公务用车购置数量',r'无[^:：。]{1,80}的(?:单位|部门)[，,]作下述说明[:：]',r'[（(]不涉及的不列[）)]']
    config['prompts']=sorted(set(m.group() for text_view in (flat_dept,flat_unit) for pattern in patterns for m in re.finditer(pattern,text_view)))
    content={'source_receipts':source_receipts,'source_pair_consistent':source_pair_consistent,'unit_source_id':unit['id'],'department_source_id':dept['id'],'report_kind':unit['report_kind'],
             'source_report_year':unit['source_report_year'],'config':config, 'pair_diff':pair_diff,
             'previous_package_id':data.get('previous_package_id'), 'changes':{}}
    if data.get('previous_package_id'):
        old=row(await conn.fetchrow('SELECT * FROM disclosure_template_packages WHERE id=$1',UUID(data['previous_package_id'])))
        if not old or old['report_kind']!=unit['report_kind']: raise HTTPException(422,'前版不存在或文种不一致')
        content['previous_source_report_year']=old['source_report_year']
        content['config']['anchors']=copy.deepcopy(old['content']['config'].get('anchors',[]))
        content['config']['accept_native_toc_variant']=old['content']['config'].get('accept_native_toc_variant',False)
        for kind in ('unit','department'):
            content['changes'][kind]=compare_text(await text(conn,old[kind+'_source_id']),await text(conn,content[kind+'_source_id']))
    identifier=uuid4()
    async with conn.transaction():
        await conn.execute("INSERT INTO disclosure_template_drafts(id,revision,status,data,actor) VALUES($1,1,'draft',$2::jsonb,$3)",identifier,canonical(content),actor)
        await audit(conn,'draft',actor,'生成差异草稿',after={'id':str(identifier)})
    return row(await conn.fetchrow('SELECT * FROM disclosure_template_drafts WHERE id=$1',identifier))


async def review(conn, identifier, revision, review_record, actor):
    if len(str(review_record.get('basis','')).strip())<3 or not review_record.get('visual_verified'):
        raise HTTPException(422,'须对照渲染原件核实编号、表格及条件并填写审核依据')
    async with conn.transaction():
        old=row(await conn.fetchrow('SELECT * FROM disclosure_template_drafts WHERE id=$1 FOR UPDATE',UUID(identifier)))
        if not old or old['revision']!=revision or old['status']=='published': raise HTTPException(409,'草稿版本已改变')
        data=old['data']; data['review_record']=review_record
        anchors=await reviewed_anchors(conn,data,review_record)
        data['config']['anchors']=anchors
        data['config']['prompts']=sorted(set(data['config'].get('prompts',[])+[a['text'] for a in anchors if a['kind']=='prompt']))
        data['config']['accept_native_toc_variant']=bool(review_record.get('accept_native_toc_variant'))
        if data['config']['accept_native_toc_variant']:
            for kind in ('unit','department'):
                view=''.join((await text(conn,data[kind+'_source_id'])).split())
                if not all(token in view for token in ('十一、其他重要事项说明','十一、其他重要事项的情况说明')):
                    raise HTTPException(422,'当前原件未证实限定的目录/正文原生变体')
        await conn.execute("UPDATE disclosure_template_drafts SET revision=revision+1,status='reviewed',data=$2::jsonb WHERE id=$1",UUID(identifier),canonical(data))
        await audit(conn,'review',actor,review_record['basis'],before=old,after=data)
    return row(await conn.fetchrow('SELECT * FROM disclosure_template_drafts WHERE id=$1',UUID(identifier)))


def validate_capabilities(capabilities, validation):
    if not isinstance(capabilities,dict) or set(capabilities)-CAPABILITIES or any(type(v) is not bool for v in capabilities.values()):
        raise HTTPException(422,'能力配置无效')
    for capability, enabled in capabilities.items():
        if enabled and not (validation.get(capability) or {}).get('report_reference'):
            raise HTTPException(422,'启用能力必须引用对应验证记录')
    if capabilities.get('writing_auto'):
        proof=validation.get('writing_auto') or {}
        if proof.get('independent_acceptance') is not True or type(proof.get('automatic_errors')) is not int or proof['automatic_errors']!=0 or type(proof.get('automatic_verified_count')) is not int or proof['automatic_verified_count']<1:
            raise HTTPException(422,'写法自动判定须有独立验收及实际自动判定数量和零错误记录')


async def publish(conn, identifier, revision, data, actor):
    async with conn.transaction():
        draft=row(await conn.fetchrow('SELECT * FROM disclosure_template_drafts WHERE id=$1 FOR UPDATE',UUID(identifier)))
        if not draft or draft['revision']!=revision or draft['status']!='reviewed': raise HTTPException(409,'须先审核当前草稿')
        content=draft['data']; decision=content['review_record'].get('decision')
        verify_source_receipts(content)
        if decision not in ('same','new'): raise HTTPException(422,'差异未明确，继续保留草稿')
        for kind in ('unit','department'): await text(conn,content[kind+'_source_id'])
        if any(data['capabilities'].values()) and not content.get('source_pair_consistent'):
            raise HTTPException(422,'来源尚未核实为同一套/批次，不能启用或继承检查能力')
        package_id=content.get('previous_package_id') if decision=='same' else None
        if decision=='same':
            if not package_id: raise HTTPException(422,'沿用须选择旧版')
            old=row(await conn.fetchrow('SELECT * FROM disclosure_template_packages WHERE id=$1',UUID(package_id)))
            # 来源槽位和排版须逐位置审核；配置语义变化必须形成新包。
            if semantic_config(content['config'])!=semantic_config(old['content']['config']):
                raise HTTPException(422,'来源相同但检查配置有变化，须发布新版本并验证')
            if not reviewed_slot_changes(content): raise HTTPException(422,'存在未核实或实质差异；仅逐位置审核的年度、名称槽位和排版可沿用')
            if data['capabilities']!=old['capability_scope']: raise HTTPException(422,'复用不得悄悄修改已发布能力')
        else:
            validate_capabilities(data['capabilities'],data['validation'])
            if (data['capabilities'].get('writing_auto') or data['capabilities'].get('position_checks')) and not content['config'].get('supported_pair'):
                raise HTTPException(422,'模板对缺少受支持的成对标题/表注指纹，不能启用写法能力')
            if any(data['capabilities'].values()) and not content.get('source_pair_consistent'):
                raise HTTPException(422,'模板来源尚未核实为同一套/批次，不能启用检查能力')
            package_id=str(uuid4()); frozen={**content,'capabilities':data['capabilities']}
            await conn.execute('''INSERT INTO disclosure_template_packages(id,version,report_kind,source_report_year,content,content_sha256,unit_source_id,department_source_id,predecessor_id,capability_scope,review_record,validation_record,actor)
                VALUES($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12::jsonb,$13)''',UUID(package_id),data['version'],content['report_kind'],content['source_report_year'],canonical(frozen),sha(frozen),UUID(content['unit_source_id']),UUID(content['department_source_id']),UUID(content['previous_package_id']) if content.get('previous_package_id') else None,canonical(data['capabilities']),canonical(content['review_record']),canonical(data['validation']),actor)
        await conn.execute("UPDATE disclosure_template_drafts SET revision=revision+1,status='published',package_id=$2 WHERE id=$1",UUID(identifier),UUID(package_id))
        await audit(conn,'publish' if decision=='new' else 'reuse',actor,content['review_record']['basis'],after={'package_id':package_id,'draft_id':identifier})
    return {'package_id':package_id}


async def bind(conn, data, actor):
    key=(data['region'],data.get('overlay',''),data['report_kind'],data['target_year'])
    if len(data['basis'].strip())<3: raise HTTPException(422,'须填写适用依据')
    async with conn.transaction():
        # 序列化不存在的键与已有键，修订号使陈旧页面不能覆盖另一位维护者。
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',canonical(key))
        current=await conn.fetchval('SELECT revision FROM disclosure_template_current_bindings WHERE region=$1 AND overlay=$2 AND report_kind=$3 AND target_year=$4',*key)
        if (current or 0)!=data['expected_revision']: raise HTTPException(409,'年度绑定已变化，请刷新')
        package=row(await conn.fetchrow('SELECT * FROM disclosure_template_packages WHERE id=$1',UUID(data['package_id'])))
        if not package or package['report_kind']!=data['report_kind']: raise HTTPException(422,'发布包不存在或文种不匹配')
        if sha(package['content'])!=package['content_sha256']: raise HTTPException(409,'发布包哈希校验失败')
        caps=data['capabilities']
        if set(caps)-CAPABILITIES or any(type(v) is not bool or (v and not package['capability_scope'].get(k)) for k,v in caps.items()): raise HTTPException(422,'年度能力不能超出发布包')
        if any(caps.values()):
            verify_source_receipts(package['content'])
            for side in ('unit','department'):await text(conn,package[side+'_source_id'])
        basis={'basis':data['basis'],'compatibility_reference':data.get('compatibility_reference'),
               'source_draft_id':data.get('source_draft_id'),'compatibility':data.get('compatibility')}
        adoption=data['adoption_kind']
        if adoption=='rollback':
            prior=row(await conn.fetchrow('SELECT * FROM disclosure_template_binding_revisions WHERE region=$1 AND overlay=$2 AND report_kind=$3 AND target_year=$4 AND revision=$5',*key,data.get('restore_revision',0)))
            if not prior or str(prior['package_id'])!=data['package_id'] or prior['capability_scope']!=caps:
                raise HTTPException(422,'回滚只能恢复该年度已有审核修订及其能力')
            original=binding_basis(prior)
            adoption=effective_adoption_kind(prior,original)
            # 恢复的是原修订的证据；不能用回滚请求替换证明或借回滚绕过跨年准入。
            basis.update({k:copy.deepcopy(original.get(k)) for k in ('compatibility_reference','source_draft_id','compatibility')})
            basis.update(restored_from_revision=prior['revision'],original_adoption_kind=adoption,
                         compatibility_source_revision=original.get('compatibility_source_revision',prior['revision']))
            if adoption is None and any(caps.get(k) for k in WRITING_CAPABILITIES):
                raise HTTPException(422,'历史回滚缺少原沿用方式与兼容依据来源，请重新核实后建立年度绑定')
        if adoption in ('not_received','historical_reference') and any(caps.get(k) for k in WRITING_CAPABILITIES):
            validate_compatibility(basis.get('compatibility'),package,target_year=data['target_year'],capabilities=caps)
        if adoption=='issued' and data['target_year']!=package['source_report_year']:
            raise HTTPException(422,'当年正式下发须匹配源报告年度；否则选择沿用或历史参考')
        if adoption=='verified_same':
            draft_id=basis.get('source_draft_id')
            checked=row(await conn.fetchrow('SELECT * FROM disclosure_template_drafts WHERE id=$1',UUID(draft_id))) if draft_id else None
            if not checked or checked['status']!='published' or checked['package_id']!=data['package_id'] or checked['data']['source_report_year']!=data['target_year'] or checked['data'].get('review_record',{}).get('decision')!='same':
                raise HTTPException(422,'确认无变化须关联该年度已审核且复用此包的来源比较记录')
        # 较早年度上传了当年原件且核实无实质变化（上方已校验比较记录）时可复用较晚版本；
        # 没有当年原件时，较晚版本不能冒充当年已生效，只能作历史参考。
        if (package['source_report_year'] is not None and data['target_year']<package['source_report_year']
                and adoption not in ('historical_reference','verified_same',None)):
            raise HTTPException(422,'较晚模板用于较早年度须先上传该年度原件并核实无实质变化，否则只能作为历史参考')
        new=(current or 0)+1
        await conn.execute('''INSERT INTO disclosure_template_binding_revisions(region,overlay,report_kind,target_year,revision,package_id,adoption_kind,capability_scope,basis,actor)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)''',*key,new,UUID(data['package_id']),data['adoption_kind'],canonical(caps),canonical(basis),actor)
        await conn.execute('''INSERT INTO disclosure_template_current_bindings(region,overlay,report_kind,target_year,revision) VALUES($1,$2,$3,$4,$5)
            ON CONFLICT(region,overlay,report_kind,target_year) DO UPDATE SET revision=EXCLUDED.revision''',*key,new)
        await audit(conn,'rollback' if data['adoption_kind']=='rollback' else 'bind',actor,data['basis'],before={'revision':current},after={**data,'binding_basis':basis,'revision':new})
    return {'revision':new}


async def snapshot(conn, year, kind='final', region='上海市', overlay=''):
    record=row(await conn.fetchrow('''SELECT h.*,p.content,p.content_sha256,p.source_report_year,p.version,p.unit_source_id,p.department_source_id
        FROM disclosure_template_current_bindings c JOIN disclosure_template_binding_revisions h USING(region,overlay,report_kind,target_year,revision)
        JOIN disclosure_template_packages p ON p.id=h.package_id WHERE c.region=$1 AND c.overlay=$2 AND c.report_kind=$3 AND c.target_year=$4''',region,overlay,kind,year))
    return await validated_snapshot(conn,record)


async def validated_snapshot(conn, record):
    if not record: return {'available':False,'reason':'年度模板未绑定','algorithm':ALGORITHM}
    if sha(record['content'])!=record['content_sha256']: return {'available':False,'reason':'发布包校验失败','algorithm':ALGORITHM}
    try:
        verify_source_receipts(record['content'])
        for k in ('unit','department'): await text(conn,record[k+'_source_id'])
    except (OSError,ValueError,HTTPException): return {'available':False,'reason':'原件或提取文本校验失败','algorithm':ALGORITHM}
    return qualify_binding_capabilities({'available':True,**record,'algorithm':ALGORITHM})


async def catalog(conn):
    result={name:[row(r) for r in await conn.fetch(query)] for name,query in {
        'sources':'SELECT * FROM disclosure_template_sources ORDER BY created_at DESC',
        'drafts':'SELECT * FROM disclosure_template_drafts ORDER BY created_at DESC',
        'packages':'SELECT * FROM disclosure_template_packages ORDER BY created_at DESC',
        'bindings':'SELECT * FROM disclosure_template_binding_revisions ORDER BY created_at DESC',
        'current':'SELECT * FROM disclosure_template_current_bindings',
        'audit':'SELECT * FROM disclosure_template_audit ORDER BY created_at DESC LIMIT 500'}.items()}
    result['algorithm']=ALGORITHM
    return result


def reviewed_slot_changes(content):
    """只允许逐组审核过的年度/名称槽位或纯排版；单位、数字等变化不能借槽位放行。"""
    import re
    reviews=content['review_record'].get('slot_reviews') or {}
    old_year=str(content.get('previous_source_report_year',''))
    new_year=str(content['source_report_year'])
    for side,changes in content.get('changes',{}).items():
        kinds=reviews.get(side,[])
        if len(kinds)!=len(changes): return False
        for change,kind in zip(changes,kinds):
            old='\n'.join(p['text'] for p in change['old']); new='\n'.join(p['text'] for p in change['new'])
            candidate=old
            if kind=='layout' and ''.join(old.split())==''.join(new.split()): continue
            if kind in ('report_year','comparison_year','report_and_comparison_year','annual_and_name'):
                replacements={}
                old_value=content.get('previous_source_report_year');new_value=content.get('source_report_year')
                if type(new_value) is int:
                    if kind in ('report_year','report_and_comparison_year','annual_and_name'):
                        replacements[str(old_value) if type(old_value) is int else '20XX']=str(new_value)
                    if kind in ('comparison_year','report_and_comparison_year','annual_and_name') and type(old_value) is int:
                        replacements[str(old_value-1)]=str(new_value-1)
                def approved_year(match):
                    prefix=old[:match.start()]
                    # 引用文献中的固定年份不充当报告年度槽位；金额和编码也不匹配。
                    if prefix.rfind('《')>prefix.rfind('》'):return match.group()
                    return replacements[match.group()]
                if replacements:
                    changed=re.sub(r'(?<!\d)(?:'+ '|'.join(map(re.escape,replacements))+r')(?=年)',approved_year,old)
                    candidate=changed
                    if kind!='annual_and_name' and changed!=old and changed==new:continue
            if kind in ('organization_name','annual_and_name'):
                pattern=r'(?:上海市|XX|××)[^\d，。；：:（）()]{0,40}?(?:办公室|委员会|中心|局)'
                left=list(re.finditer(pattern,candidate));right=list(re.finditer(pattern,new))
                if any(candidate[:a.start()]+b.group()+candidate[a.end():]==new for a in left for b in right):continue
            return False
    return True


async def export_bundle(conn):
    """将元数据和原件一起导出，离线验证使用整包哈希，不依赖数据库。"""
    import zipfile
    payload=await catalog(conn)
    for package in payload['packages']:
        verify_source_receipts(package['content'])
        if sha(package['content'])!=package['content_sha256']:raise HTTPException(409,'发布包校验失败，不能导出')
    entries={'catalog.json':canonical(payload).encode()}
    for item in payload['sources']:
        _,manifest,directory=await source(conn,item['id'])
        for filename in (manifest['original'],'manifest.json','extracted.txt','spreadsheet_cells.json'):
            path=directory/filename
            if path.exists():entries['sources/'+item['id']+'/'+filename]=path.read_bytes()
    entries['checksums.json']=canonical({name:hashlib.sha256(value).hexdigest() for name,value in entries.items()}).encode()
    target=storage()/'exports'/(str(uuid4())+'.zip');target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,value in entries.items():archive.writestr(name,value)
    return target


def offline_snapshot(path, expected_sha256, year, kind='final', region='上海市', overlay=''):
    import zipfile,io
    try:
        data=Path(path).read_bytes()
    except OSError:
        return {'available':False,'reason':'离线批准导出包读取失败','algorithm':ALGORITHM}
    if not expected_sha256 or hashlib.sha256(data).hexdigest()!=expected_sha256:
        return {'available':False,'reason':'离线批准导出包校验失败','algorithm':ALGORITHM}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            checksums=json.loads(archive.read('checksums.json'))
            for name,digest in checksums.items():
                if hashlib.sha256(archive.read(name)).hexdigest()!=digest:raise ValueError('export member hash mismatch')
            payload=json.loads(archive.read('catalog.json'))
            key=next((x for x in payload['current'] if (x['region'],x['overlay'],x['report_kind'],x['target_year'])==(region,overlay,kind,year)),None)
            if not key:return {'available':False,'reason':'年度模板未绑定','algorithm':ALGORITHM}
            binding=next(x for x in payload['bindings'] if all(x[k]==v for k,v in key.items()))
            package=next(x for x in payload['packages'] if x['id']==binding['package_id'])
            if sha(package['content'])!=package['content_sha256']:raise ValueError('package hash mismatch')
            return qualify_binding_capabilities({**binding,'content':package['content'],'content_sha256':package['content_sha256'],
                'source_report_year':package['source_report_year'],'available':True,'algorithm':ALGORITHM,'offline_export_sha256':expected_sha256})
    except (OSError,ValueError,KeyError,StopIteration,zipfile.BadZipFile):
        return {'available':False,'reason':'离线模板内容校验失败','algorithm':ALGORITHM}


def validate_compatibility(proof, package, *, target_year, capabilities):
    """n 是独立兼容样本，m 是有真值的自动确定子集，不能用自动覆盖率代替验收。"""
    if (not isinstance(proof,dict) or not isinstance(proof.get('report_reference'),str)
        or not proof['report_reference'].strip() or proof.get('independent_labels') is not True
        or proof.get('boundary_cases_passed') is not True):
        raise HTTPException(422,'沿用能力须有独立标注、关键边界和年度兼容验证记录')
    n=proof.get('independent_sample_count'); m=proof.get('automatic_verified_count'); errors=proof.get('automatic_errors')
    corpus=proof.get('corpus_size')
    if type(n) is not int or n<1:
        raise HTTPException(422,'须记录独立核实的兼容样本数；旧自动确定数不能补作独立样本数')
    if (type(m) is not int or not 0<=m<=n or type(errors) is not int or not 0<=errors<=m or errors!=0):
        raise HTTPException(422,'自动确定数须在独立样本内，错误数须为实际整数且为零')
    if corpus is not None and (type(corpus) is not int or corpus<1 or n>corpus):
        raise HTTPException(422,'独立兼容样本数不能超过实际目标语料总数')
    if type(proof.get('all_corpus_verified',False)) is not bool:
        raise HTTPException(422,'全量核实声明无效')
    if proof.get('all_corpus_verified') is True and (corpus is None or n!=corpus):
        raise HTTPException(422,'全量核实声明须与实际语料总数和独立样本数一致')
    if n<100 and not (type(corpus) is int and 0<corpus<100 and n==corpus and proof.get('all_corpus_verified') is True):
        raise HTTPException(422,'独立兼容样本至少100份；小语料须全量核实，不要求自动确定数达到100')
    scope=proof.get('capability_scope')
    if (not isinstance(scope,dict) or set(scope)-CAPABILITIES or any(type(v) is not bool for v in scope.values())
        or any(capabilities.get(k) and not scope.get(k) for k in WRITING_CAPABILITIES)):
        raise HTTPException(422,'兼容验证未覆盖请求的写法能力范围')
    if capabilities.get('writing_auto') and m<1:
        raise HTTPException(422,'写法自动判定尚无独立核实的自动确定结果；可单独核实位置检查能力')
    if (type(proof.get('source_report_year')) is not int or proof['source_report_year']!=package.get('source_report_year')
        or type(proof.get('target_year')) is not int or proof['target_year']!=target_year
        or proof.get('package_hash')!=package['content_sha256'] or proof.get('algorithm')!=ALGORITHM):
        raise HTTPException(422,'兼容验证的源年度、目标年度、模板哈希或算法版本不匹配')


def binding_basis(record):
    value=record.get('basis')
    if isinstance(value,str):
        try:value=json.loads(value)
        except (ValueError,TypeError):return {}
    return value if isinstance(value,dict) else {}


def effective_adoption_kind(record, basis):
    adoption=record.get('adoption_kind')
    if adoption!='rollback':return adoption
    original=basis.get('original_adoption_kind'); revision=basis.get('restored_from_revision')
    if type(revision) is int and revision>0 and original in ('issued','verified_same','not_received','historical_reference'):
        return original
    return None


def qualify_binding_capabilities(record):
    """仅为新分析投影有效能力；批准记录、依据和历史快照保持原样。"""
    result=copy.deepcopy(record); caps=result.get('capability_scope') or {}
    result['approved_capability_scope']=copy.deepcopy(caps)
    basis=binding_basis(result); adoption=effective_adoption_kind(result,basis); pending={}
    for capability in WRITING_CAPABILITIES:
        if not caps.get(capability):continue
        try:
            if adoption in ('not_received','historical_reference'):
                validate_compatibility(basis.get('compatibility'),result,target_year=result.get('target_year'),capabilities={capability:True})
            elif adoption=='issued':
                if result.get('target_year')!=result.get('source_report_year'):
                    raise HTTPException(422,'正式下发年度与源年度不匹配')
            elif adoption=='verified_same':
                if not basis.get('source_draft_id'):
                    raise HTTPException(422,'确认无变化缺少已审核的来源比较记录')
            else:
                raise HTTPException(422,'原沿用方式或回滚证据来源尚未核实')
        except HTTPException as exc:
            caps[capability]=False; pending[capability]=str(exc.detail)
    result['capability_scope']=caps
    if pending:
        result['compatibility_pending']=pending
        result['reason']='年度写法能力兼容依据待核：'+'；'.join(sorted(set(pending.values())))
    return result


async def reviewed_anchors(conn, content, review_record):
    """模板差异只有经明确分类、规范依据与原件定位审核后才能新增检查。"""
    import re,unicodedata
    anchors=[]
    inherited=[]
    if content.get('previous_package_id'):
        prior=row(await conn.fetchrow('SELECT * FROM disclosure_template_packages WHERE id=$1',UUID(content['previous_package_id'])))
        inherited=(prior['content']['config'].get('anchors') or []) if prior else []
    for item in review_record.get('anchors',[]):
        if not isinstance(item.get('text'),str) or not isinstance(item.get('basis'),str) or not isinstance(item.get('source_id'),str):raise HTTPException(422,'锚点原文、依据和来源必须为文本')
        if item.get('kind') not in ('normative_section','normative_table','prompt','style','optional') or item.get('scope') not in ('all','unit','department'):
            raise HTTPException(422,'模板锚点类别或适用条件无效')
        if item.get('source_id') not in (content['unit_source_id'],content['department_source_id']) and item not in inherited:
            raise HTTPException(422,'变更锚点须来自当前成对原件；旧锚点只可完整继承')
        needle=''.join(unicodedata.normalize('NFKC',item.get('text','')).split())
        if not 3<=len(needle)<=200 or len(item.get('basis','').strip())<3:raise HTTPException(422,'须填写完整锚点原文和规范/分类依据')
        raw=await text(conn,item['source_id']); chars=[(i,c) for i,char in enumerate(raw) for c in unicodedata.normalize('NFKC',char) if not c.isspace()]
        view=''.join(c for _,c in chars);matches=list(re.finditer(re.escape(needle),view))
        occurrence=item.get('occurrence')
        if not matches or (len(matches)>1 and occurrence is None):raise HTTPException(422,'原文未唯一定位，请指定已核实的第几处')
        occurrence=occurrence if occurrence is not None else 1
        if type(occurrence) is not int or not 1<=occurrence<=len(matches):raise HTTPException(422,'锚点位置序号无效')
        match=matches[occurrence-1];start=chars[match.start()][0];end=chars[match.end()-1][0]+1
        if item['kind']=='prompt' and not re.search('列明|仍保留该表格式|按上述格式|不涉及的不列|如持平|作下述说明|根据三定方案',needle):
            raise HTTPException(422,'尚未支持此类提示语自动检查；可登记为样式或可选内容')
        original,_,_=await source(conn,item['source_id'])
        org_slot=''.join(str(item.get('organization_slot') or '').split())
        if org_slot and (org_slot not in needle or not re.fullmatch(r'[^0-9，。；：]{3,80}(?:局|办公室|委员会|中心|院|厅|馆|署|办)',org_slot)):
            raise HTTPException(422,'单位名称槽位须为原文中的完整机构名称')
        cell_path=source_dir(item['source_id'])/'spreadsheet_cells.json'
        source_cells=[]
        if cell_path.exists():
            all_cells=json.loads(cell_path.read_text())
            source_cells=[{k:cell[k] for k in ('sheet','row','column','cell_type')} for cell in all_cells
                if cell.get('text_start',-1)<end and cell.get('text_end',-1)>start]
            if not source_cells:raise HTTPException(422,'请选择单元格原文，工作表名称不能充当规范依据')
        from src.services.template_anchor_slots import validated_slots
        try:slots=validated_slots({**item,'text':needle},original['source_report_year'])
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        for slot in slots:
            slot['source_start']=chars[match.start()+slot['start']][0]
            slot['source_end']=chars[match.start()+slot['end']-1][0]+1
            slot['source_quote']=raw[slot['source_start']:slot['source_end']]
        anchors.append({**item,'text':needle,'start':start,'end':end,'raw_text':raw[start:end],
                        'organization_slot':org_slot,'source_report_year':original['source_report_year'],'source_cells':source_cells,'slots':slots})
    return anchors


def semantic_config(config):
    """来源和位置变化单独留痕；比较实际检查语义，避免为槽位或原件复制规则。"""
    value=copy.deepcopy(config)
    mapped=[]
    for item in value.get('anchors') or []:
        from src.services.template_anchor_slots import instantiate_anchor
        text=instantiate_anchor(item,item.get('source_report_year'),None,semantic=True)
        mapped.append({k:v for k,v in item.items() if k in ('kind','scope','basis')} | {'text':text})
    value['anchors']=sorted(mapped,key=canonical)
    value.setdefault('accept_native_toc_variant',False)
    return value


def geography(keys, profile, pages):
    """只用合格封面和已批准的适用键选择覆盖层，文件名不提供地区证明。"""
    import re
    cover=''.join((pages[0] if pages else '').split())
    jurisdiction=str(getattr(getattr(profile,'jurisdiction',None),'value','') or '')
    regions={k['region'] for k in keys}
    matches=[name for name in regions if jurisdiction.startswith(name) and jurisdiction in cover]
    if not matches:matches=[name for name in regions if name in cover]
    if len(matches)>1:return None,None,'原文地区与多个适用范围匹配，需核实'
    region=matches[0] if matches else '上海市'
    if not matches and re.match(r'[^区县]{2,8}市',jurisdiction) and jurisdiction in cover and not jurisdiction.startswith('上海市'):
        return None,None,'原文地区没有对应已批准模板'
    overlays=[k['overlay'] for k in keys if k['region']==region and k['overlay'] and k['overlay'] in cover and any(k.get('capability_scope',{}).values())]
    if len(overlays)>1:return None,None,'原文命中多个区县覆盖层，需核实'
    return region,overlays[0] if overlays else '',None


def compose_snapshots(base, overlay):
    if not overlay:return base
    if not overlay.get('available'):return overlay
    result=copy.deepcopy(overlay)
    result['municipal_reference']={k:base.get(k) for k in ('available','reason','package_id','content_sha256','revision')}
    if base.get('available'):
        base_config=base['content']['config']; config=copy.deepcopy(result['content']['config'])
        config['anchors']=[*base_config.get('anchors',[]),*config.get('anchors',[])]
        config['prompts']=sorted(set(base_config.get('prompts',[])+config.get('prompts',[])))
        result['effective_config']=config; result['effective_config_sha256']=sha(config)
        # 类型/位置能力由覆盖层自身的验收决定，规范与已审核提示语不能因覆盖层省略而取消。
        caps=result['capability_scope']
        for capability in ('structure','residue'):
            caps[capability]=bool(caps.get(capability) or base['capability_scope'].get(capability))
    return result


async def select_for_profile(conn, profile, pages):
    keys=[row(k) for k in await conn.fetch('''SELECT h.region,h.overlay,h.capability_scope FROM disclosure_template_current_bindings c
        JOIN disclosure_template_binding_revisions h USING(region,overlay,report_kind,target_year,revision)
        WHERE c.report_kind=$1 AND c.target_year=$2''',profile.kind,profile.year)]
    region,overlay,error=geography(keys,profile,pages)
    if error:return {'available':False,'reason':error,'algorithm':ALGORITHM,'target_year':profile.year}
    base=await snapshot(conn,profile.year,profile.kind,region)
    return compose_snapshots(base,await snapshot(conn,profile.year,profile.kind,region,overlay) if overlay else None)


def offline_for_profile(path, expected_sha256, profile, pages):
    import zipfile,io
    try:
        data=Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected_sha256:raise ValueError('unapproved export')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:payload=json.loads(archive.read('catalog.json'))
        keys=[]
        for current in payload['current']:
            if current['target_year']!=profile.year or current['report_kind']!=profile.kind:continue
            binding=next(x for x in payload['bindings'] if all(x[k]==v for k,v in current.items()))
            keys.append(binding)
        region,overlay,error=geography(keys,profile,pages)
        if error:return {'available':False,'reason':error,'algorithm':ALGORITHM,'target_year':profile.year}
        base=offline_snapshot(path,expected_sha256,profile.year,profile.kind,region)
        return compose_snapshots(base,offline_snapshot(path,expected_sha256,profile.year,profile.kind,region,overlay) if overlay else None)
    except (OSError,ValueError,KeyError,StopIteration,zipfile.BadZipFile):
        return {'available':False,'reason':'离线模板覆盖层校验失败','algorithm':ALGORITHM}


def source_receipt(identifier):
    """把提取视图和单元格映射也绑定到不可变发布内容，防止旁改 manifest 隐藏篡改。"""
    directory=source_dir(identifier)
    return {filename:hashlib.sha256((directory/filename).read_bytes()).hexdigest()
        for filename in ('manifest.json','extracted.txt','spreadsheet_cells.json') if (directory/filename).exists()}


def verify_source_receipts(content):
    receipts=content.get('source_receipts') or {}
    for kind in ('unit','department'):
        expected=receipts.get(kind)
        if not expected or expected!=source_receipt(content[kind+'_source_id']):
            raise HTTPException(409,'原件提取清单或位置映射校验失败，请重新导入审核')


async def freeze_current_bindings(conn):
    """任务开始时用一条 SQL 固定绑定与不可变包；解析后不再读取当前指针。"""
    return [row(item) for item in await conn.fetch("""SELECT h.*,p.content,p.content_sha256,
        p.source_report_year,p.version,p.unit_source_id,p.department_source_id
        FROM disclosure_template_current_bindings c JOIN disclosure_template_binding_revisions h
        USING(region,overlay,report_kind,target_year,revision)
        JOIN disclosure_template_packages p ON p.id=h.package_id""")]


async def select_frozen(conn, frozen, profile, pages):
    candidates=[item for item in frozen if item['report_kind']==profile.kind and item['target_year']==profile.year]
    region,overlay,error=geography(candidates,profile,pages)
    if error:return {'available':False,'reason':error,'algorithm':ALGORITHM}
    async def selected(layer):
        item=next((copy.deepcopy(x) for x in candidates if x['region']==region and x['overlay']==layer),None)
        return await validated_snapshot(conn,item)
    return compose_snapshots(await selected(''),await selected(overlay) if overlay else None)

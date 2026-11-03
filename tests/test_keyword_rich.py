import base64
import copy
import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import test_main as fixtures
from keyword_rules import KeywordReply, keyword_reply_for_message, normalize_rule

module = fixtures.module
images_module = __import__(module.__package__ + '.keyword_images', fromlist=['KeywordImages'])
web_module = __import__(module.__package__ + '.web', fromlist=['request'])


def png(color='red'):
    out = BytesIO()
    Image.new('RGB', (4, 4), color).save(out, 'PNG')
    return out.getvalue()


class KeywordRulesTest(unittest.TestCase):
    def rule(self, **kw):
        return dict(name='测试', keywords='帮助', reply='文字', **kw)

    def test_random_rich_reply_preserves_pairs(self):
        rules = [self.rule(responses=[{'text': '甲', 'images': ['a'*64+'.png']}, {'text': '乙', 'images': []}])]
        with patch('keyword_rules.random.choice', side_effect=lambda values: values[0]):
            self.assertEqual(keyword_reply_for_message('帮助', 'g', rules, []), KeywordReply('甲', ('a'*64+'.png',)))
        with patch('keyword_rules.random.choice', side_effect=lambda values: values[1]):
            self.assertEqual(keyword_reply_for_message('帮助', 'g', rules, []), KeywordReply('乙'))

    def test_regex_capture_and_command_isolation(self):
        rule = dict(name='作法', keywords=r'(.+?)作法', match_type='regex', reply='{1} 殿下，XX好', trigger='command')
        self.assertIsNone(keyword_reply_for_message('小明作法', 'g', [rule], [], is_command=False))
        self.assertEqual(keyword_reply_for_message('小明作法 参数', 'g', [rule], [], is_command=True).text, '小明 殿下，小明好')
        rule['trigger'] = 'auto'
        self.assertIsNone(keyword_reply_for_message('小明作法', 'g', [rule], [], is_command=True))
        rule['keywords'] = r'(a{1,3})作法'
        self.assertEqual(keyword_reply_for_message('aa作法', 'g', [rule], []).text, 'aa 殿下，aa好')

    def test_capture_is_literal_and_cannot_create_empty_message(self):
        rule = dict(name='捕获', keywords=r'(.*?)作法', match_type='regex', reply='{1}/XX')
        self.assertEqual(keyword_reply_for_message('XXabc作法', 'g', [rule], []).text, 'XXabc/XXabc')
        rule['reply'] = '{1}'
        self.assertIsNone(keyword_reply_for_message('作法', 'g', [rule], []))

    def test_regex_budget_and_invalid_input(self):
        bad = dict(name='耗时', keywords='(a+)+$', match_type='regex', reply='错误')
        started = time.monotonic()
        self.assertIsNone(keyword_reply_for_message('a'*4000+'!', 'g', [bad]*100, []))
        self.assertLess(time.monotonic()-started, 0.5)
        for change in ({'keywords': '(','match_type':'regex'}, {'responses':[{'text':'','images':[]}]}, {'responses':[{'images':['../x.png']}]}, {'trigger':'wrong'}, {'case_sensitive':'false'}):
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                normalize_rule({**self.rule(), **change})

    def test_legacy_schema_defaults_case_and_scope(self):
        rule = dict(name='旧规则', keyword='HELLO', reply='旧内容', responses=[])
        self.assertEqual(keyword_reply_for_message('hello', 'g', [rule], []).text, '旧内容')
        rule['case_sensitive'] = True
        self.assertIsNone(keyword_reply_for_message('hello', 'g', [rule], []))
        rule.update(case_sensitive=False, group_openids='another')
        self.assertIsNone(keyword_reply_for_message('hello', 'g', [], [rule]))


class KeywordImagesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = images_module.KeywordImages(Path(self.temp.name))

    def test_upload_dedup_preview_and_restart(self):
        data = png()
        item = self.store.upload(base64.b64encode(data).decode())
        self.assertEqual(self.store.upload(base64.b64encode(data).decode()), item)
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)
        restarted = images_module.KeywordImages(Path(self.temp.name))
        self.assertEqual(restarted.read(item['id']), data)
        thumbnail = base64.b64decode(restarted.preview(item['id'])['data_url'].split(',')[1])
        with Image.open(BytesIO(thumbnail)) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertLessEqual(image.width, 360)
            self.assertLessEqual(image.height, 240)

    def test_preview_is_bounded_without_changing_original(self):
        out = BytesIO()
        Image.new('RGB', (800, 400), 'red').save(out, 'PNG')
        data = out.getvalue()
        image_id = self.store.upload(base64.b64encode(data).decode())['id']
        preview = base64.b64decode(self.store.preview(image_id)['data_url'].split(',')[1])
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual(image.size, (360, 180))
        self.assertEqual(self.store.read(image_id), data)

    def test_reject_nonimage_path_symlink_and_missing(self):
        for data in (b'<svg onload="alert(1)"></svg>', b'not an image', png()[:20]):
            with self.assertRaises(ValueError):
                self.store.upload(base64.b64encode(data).decode())
        for name in ('../secret', '/tmp/secret', 'https://x/a.jpg', 'a.png'):
            with self.assertRaises(ValueError):
                self.store.read(name)
        with self.assertRaises(LookupError):
            self.store.read('a'*64+'.png')
        target = Path(self.temp.name)/('a'*64+'.png')
        target.symlink_to(Path(self.temp.name)/'secret')
        with self.assertRaises(ValueError):
            self.store.read(target.name)

    def test_pixel_and_storage_budgets(self):
        with patch.object(images_module, 'MAX_PIXELS', 10), self.assertRaises(ValueError):
            self.store.upload(base64.b64encode(png()).decode())
        with patch.object(images_module, 'STORE_BUDGET', 1), self.assertRaises(ValueError):
            self.store.upload(base64.b64encode(png()).decode())
        with self.assertRaises(ValueError):
            self.store.upload('!invalid')
        with patch.object(images_module, 'MAX_BASE64', 4), self.assertRaises(ValueError):
            self.store.upload('12345678')


class KeywordFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin, self.client = fixtures.PluginFlowTest().plugin()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plugin._keyword_images = images_module.KeywordImages(Path(self.temp.name))
        self.ids = [self.plugin.keyword_images().upload(base64.b64encode(png(color)).decode())['id'] for color in ('red','blue')]
        self.rule = {'name':'图文', 'keywords':'帮助', 'responses':[{'text':'看这里', 'images':self.ids}], 'match_type':'exact'}
        self.entry = self.plugin.config['auto_review_groups'][0]
        self.entry['keyword_replies'] = [self.rule]
        self.event = fixtures.FakeEvent(self.client, '帮助')
        self.api = SimpleNamespace(upload_group_image=AsyncMock(return_value={'file_info':'uploaded'}))

    async def reply(self):
        with patch.object(module, 'QQGroupAPI', return_value=self.api):
            return await self.plugin._reply_to_keyword(self.event, 'group-1', 'incoming', '帮助', self.entry)

    async def test_images_text_msgid_sequence_and_recall(self):
        self.plugin.config['keyword_reply_recall_seconds'] = 10
        with patch.object(self.plugin, '_schedule_recall') as recall:
            self.assertTrue(await self.reply())
        self.assertEqual(self.api.upload_group_image.await_count, 2)
        first, second = self.client.api.messages
        self.assertEqual(first['content'], '看这里')
        self.assertNotIn('content', second)
        self.assertEqual([m['msg_type'] for m in (first,second)], [7,7])
        self.assertEqual([m['msg_id'] for m in (first,second)], ['incoming','incoming'])
        self.assertNotEqual(first['msg_seq'], second['msg_seq'])
        self.assertEqual(recall.call_count, 2)
        self.assertTrue(self.event.stopped)

    async def test_image_only_reply(self):
        self.rule['responses'][0]['text'] = ''
        self.assertTrue(await self.reply())
        self.assertTrue(all('content' not in message for message in self.client.api.messages))

    async def test_missing_image_save_rejected_without_mutation(self):
        web = self.plugin._web
        rule = copy.deepcopy(self.rule)
        rule['responses'][0]['images'] = ['d'*64+'.png']
        with patch.object(web, '_payload', AsyncMock(return_value={'rules':[rule]})), \
             patch.object(web, '_scope_group_openids', AsyncMock(return_value={'group-1'})), \
             patch.object(self.plugin, 'web_save_global_keyword_replies', AsyncMock()) as save:
            with self.assertRaises(LookupError):
                await web.page_global_keyword_replies_save()
            save.assert_not_awaited()

    async def test_upload_failure_restores_cooldown_without_sending(self):
        self.plugin.config['keyword_reply_cooldown_seconds'] = 60
        self.api.upload_group_image.side_effect = RuntimeError('upload denied')
        self.assertFalse(await self.reply())
        self.assertEqual(self.client.api.messages, [])
        self.assertNotIn('group-1', self.plugin._keyword_reply_ready_at)

    async def test_partial_send_recalled_and_no_ai_fallback(self):
        self.plugin.config.update(keyword_reply_cooldown_seconds=60, keyword_reply_recall_seconds=10)
        self.client.api.post_group_message = AsyncMock(side_effect=[{'id':'first'}, TimeoutError('uncertain')])
        with patch.object(self.plugin, '_schedule_recall') as recall:
            self.assertTrue(await self.reply())
        recall.assert_called_once_with(self.client,'group-1','first',10,'keyword-reply')
        self.assertTrue(self.event.stopped)
        self.assertFalse(await self.reply())
        self.assertEqual(self.client.api.post_group_message.await_count, 2)

    async def test_global_and_group_roundtrip_validation(self):
        web = self.plugin._web
        rules = web._global_keyword_replies([{**self.rule,'group_openids':['group-1'],'trigger':'auto','case_sensitive':True}], {'group-1'})
        await web._validate_keyword_images(rules)
        result = await self.plugin.web_save_global_keyword_replies({'rules': rules})
        self.assertEqual(result['rules'][0]['responses'], self.rule['responses'])
        self.assertEqual(result['rules'][0]['trigger'], 'auto')
        self.assertTrue(result['rules'][0]['case_sensitive'])
        group_rules = web._keyword_replies(result['rules'])
        self.assertEqual(group_rules[0]['responses'], self.rule['responses'])
        missing = copy.deepcopy(rules)
        missing[0]['responses'][0]['images'] = ['c'*64+'.png']
        with self.assertRaises(LookupError):
            await web._validate_keyword_images(missing)

    async def test_upload_preview_routes_and_large_body(self):
        web = self.plugin._web
        request = SimpleNamespace(headers={}, body=AsyncMock(return_value=json.dumps({'base64':base64.b64encode(png()).decode()}).encode()))
        with patch.object(web_module, 'request', request):
            result = await web.page_keyword_image_upload()
        self.assertEqual(result['data']['id'], self.ids[0])
        request.json = AsyncMock(return_value={'id': self.ids[0]})
        with patch.object(web_module, 'request', request):
            self.assertTrue((await web.page_keyword_image_preview())['data']['data_url'].startswith('data:image/png;base64,'))
        request.headers = {'content-length':str(web_module.MAX_BASE64+1025)}
        request.body.reset_mock()
        with patch.object(web_module, 'request', request), self.assertRaises(ValueError):
            await web.page_keyword_image_upload()
        request.body.assert_not_awaited()

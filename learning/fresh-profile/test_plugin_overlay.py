import unittest
from plugin_overlay import BASE, overlay

class OverlayTest(unittest.TestCase):
    def test_pins_and_scope(self):
        d={'kind':'Deployment','metadata':{'name':'argo-rollouts','namespace':'fresh-control'},'spec':{'template':{'spec':{'serviceAccountName':'argo-rollouts','containers':[{'image':BASE}]}}}}
        image='local/account-rollouts@sha256:'+'a'*64
        out=overlay(d,image,'b'*64)
        self.assertEqual(out[0]['spec']['template']['spec']['containers'][0]['image'],image)
        self.assertEqual(out[3]['subjects'],[{'kind':'ServiceAccount','name':'argo-rollouts','namespace':'fresh-control'}])
        self.assertTrue(all(set(r['verbs']) <= {'get','list','watch'} for r in out[2]['rules']))
        self.assertEqual(d['spec']['template']['spec']['containers'][0]['image'],BASE)
        for bad in ('local/rollouts:latest','', 'local/rollouts@sha256:bad'):
            with self.assertRaises(ValueError): overlay(d,bad,'b'*64)
        with self.assertRaises(ValueError): overlay(d,image,'bad')
        with self.assertRaises(ValueError): overlay(out[0],image,'b'*64)

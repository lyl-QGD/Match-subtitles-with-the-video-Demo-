import unittest
from app.media.subtitles import parse_srt, align

def cue(seq, start, end, text='text'):
    return {'seq':seq,'start':start,'end':end,'text':text,'raw':text}

class SubtitleTests(unittest.TestCase):
    def test_bom_and_multiline(self):
        data='1\n00:00:01,010 --> 00:00:02,020\nhello\nworld\n'.encode('utf-8-sig')
        row=parse_srt(data)[0]
        self.assertEqual((row['start'],row['end']),(1010,2020))
        self.assertEqual(row['raw'],'hello\nworld')

    def test_one_to_many_and_missing_language(self):
        groups=align({'en':[cue(1,0,2000),cue(2,4000,5000)],'zh':[cue(1,0,1000),cue(2,1000,2000)]},6000)
        self.assertEqual(len(groups),2)
        self.assertEqual(len(groups[0]['nodes']),3)
        self.assertIn('missing_language',groups[1]['flags'])

    def test_many_to_one(self):
        groups=align({'en':[cue(1,0,1000),cue(2,1000,2000)],'zh':[cue(1,0,2000)]},3000)
        self.assertEqual(len(groups),1)

    def test_touching_boundaries_do_not_merge(self):
        groups=align({'en':[cue(1,0,1000)],'zh':[cue(1,1000,2000)]},3000)
        self.assertEqual(len(groups),2)

    def test_offset_and_bounds(self):
        self.assertEqual(align({'en':[cue(1,100,200)],'zh':[]},1000,50)[0]['start'],150)
        with self.assertRaises(ValueError):
            align({'en':[cue(1,100,200)],'zh':[]},1000,-150)

    def test_invalid_time_rejected(self):
        with self.assertRaises(ValueError):
            parse_srt(b'1\n00:00:03,000 --> 00:00:01,000\nbad')

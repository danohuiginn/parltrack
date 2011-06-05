#!/usr/bin/env python
# -*- coding: utf-8 -*-
#    This file is part of parltrack.

#    parltrack is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    parltrack is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.

#    You should have received a copy of the GNU Affero General Public License
#    along with parltrack.  If not, see <http://www.gnu.org/licenses/>.

# (C) 2011 by Stefan Marsiske, <stefan.marsiske@gmail.com>

# externally depends on wvHtml

from lxml.html.soupparser import parse
from lxml.etree import tostring
from tempfile import mkdtemp, mkstemp
import urllib2, json, sys, subprocess, os, re, unicodedata
from cStringIO import StringIO
from datetime import datetime
from ep_meps import group_map, groupids as Groupids

def fetchVotes(d):
    url="%s%s%s" % ("http://www.europarl.europa.eu/sides/getDoc.do?pubRef=-//EP//NONSGML+PV+",
                    d,
                    "+RES-RCV+DOC+WORD+V0//EN&language=EN")
    print >>sys.stderr, url
    f=urllib2.urlopen(url)
    tmp=mkstemp()
    fd=os.fdopen(tmp[0],'w')
    fd.write(f.read())
    fd.close()
    f.close()
    res=subprocess.Popen(['/usr/bin/wvHtml', tmp[1], '-'],
                     stdout=subprocess.PIPE).communicate()[0]
    os.unlink(tmp[1])
    return parse(StringIO(res))

def dateJSONhandler(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    elif type(obj)==ObjectId:
        return str(obj)
    else:
        raise TypeError, 'Object of type %s with value of %s is not JSON serializable' % (type(obj), repr(obj))

def splitMeps(text, res, date):
    for q in text.split('/'):
        res['rapporteur'].append(q)

def scanMeps(text, res, date):
    tmp=text.split(':')
    if len(tmp)==2:
        return splitMeps(tmp[1], res, date)
    elif len(tmp)==1:
        return splitMeps(text, res, date)
    else:
        print >>sys.stderr, 'huh', line

docre=re.compile(u'(.*)((?:[AB]|RC)[67]\s*-\s*[0-9]{3,4}\/[0-9]{4})(.*)')
tailre=re.compile(r'^(?:\s*-\s*)?(.*)\s*-\s*([^-]*$)')
junkdashre=re.compile(r'^[ -]*(.*)[ -]*$')
rapportre=re.compile(r'(.*)(?:recommendation|rapport|report):?\s?(.*)',re.I)
def votemeta(line, date):
    print >>sys.stderr, line.encode('utf8')
    res={'rapporteur': []}
    m=docre.search(line)
    if m:
        line=''.join([m.group(1),m.group(3)])
        doc=m.group(2).replace(' ', '')
        res['report']=doc
    m=tailre.match(line)
    if m:
        res['issue_type']=m.group(2).strip()
        line=m.group(1).strip()
    if line.startswith('RC'):
        res['RC']=True
        line=line[2:].strip()
    m=junkdashre.match(line)
    if m:
        line=m.group(1)
    tmp=line.split(' - ')
    if len(tmp)>1:
        if len(tmp)>2:
            print >>sys.stderr, "many ' - '",line
        line=tmp[0]
        res['issue_type']="%s %s" % (' - '.join(tmp[1:]),res.get('issue_type',''))
    line=line.strip()
    if not line:
        return res
    if line.endswith('()'):
        line=line[:-2].strip()
    m=rapportre.search(line)
    if m:
        # handle mep
        if m.group(2):
            scanMeps(m.group(2),res, date)
        else:
            scanMeps(m.group(1),res, date)
    return res

reportre=re.compile(r'(Report: .*) ?- ?(.*)$')
def scrape(f):
    tree=fetchVotes(f)

    res=[]
    items=tree.xpath('//div[@name="Heading 1"]')
    if not items:
        items=tree.xpath('//div[@name="VOTE INFO TITLE"]')
    for issue in items:
        # get rapporteur, report id and report type
        tmp=issue.xpath('string()').strip()
        vote={}
        # get timestamp
        vote['ts']= datetime.strptime(issue.xpath('following::td')[0].xpath('string()').strip().replace('.000',''),
                          "%d/%m/%Y %H:%M:%S")
        vote['title']=tmp
        vote.update(votemeta(tmp, vote['ts']))
        # get the +/-/0 votes
        for decision in issue.xpath('ancestor::table')[0].xpath("following::table")[0:3]:
            tmp=[x.strip() for x in decision.xpath('.//text()') if x.strip()]
            total,k=tmp[0],''.join(tmp[1:])
            vtype=''.join([x.strip() for x in k.split('-')])
            if u'Υπέρ' in vtype or u'ΥΠΕΡ' in vtype:
                k="+"
            if u'Κατά' in vtype or u'ΚΑΤΑ' in vtype:
                k="-"
            if u'Απoχές' in vtype or u'ΑΠOΧΕΣ' in vtype:
                k="0"
            vote[k]={'total': total}
            for cur in decision.xpath('../following-sibling::*'):
                group=cur.xpath('.//b/text()')
                if group and ''.join([x.strip() for x in group]) in Groupids:
                    next=group[0].getparent().xpath('following-sibling::*/text()')
                    if next and next[0]==group[1]:
                        group=''.join(group[:2]).strip()
                    else:
                        group=group[0].strip()
                    voters=[x.strip() for x in cur.xpath('.//b/following-sibling::text()')[0].split(',') if x.strip()]
                    if not voters: continue
                    # strip of ":    " after the group name
                    if voters[0][0]==':': voters[0]=voters[0][1:].strip()
                    vtmp=[]
                    for name in voters:
                        vtmp.append(name)
                    vote[k][group]=vtmp
                if cur.xpath('.//table'):
                    break
        # get the correctional votes
        try:
            cor=issue.xpath('ancestor::table')[0].xpath("following::table")[3]
        except IndexError:
            res.append(vote)
            continue
        try:
            has_corr=' '.join([x for x in cor.xpath('tr')[0].xpath('.//text()') if x.strip()]).find(u"ПОПРАВКИ В ПОДАДЕНИТЕ ГЛАСОВЕ И НАМЕРЕНИЯ ЗА ГЛАСУВАНЕ")!=-1
        except IndexError:
            has_corr=None
        if not has_corr:
            res.append(vote)
            continue
        skip=False
        for row in cor.xpath('tr')[1:]:
            if skip:
                skip=False
                continue
            try:
                k,voters=[x.xpath('string()').strip() for x in row.xpath('td') if x.xpath('string()').find(u"ПОПРАВКИ В ПОДАДЕНИТЕ ГЛАСОВЕ И НАМЕРЕНИЯ ЗА ГЛАСУВАНЕ")==-1]
            except ValueError:
                # votes between 2006 and 2007 have another correction table format with separate tr-s
                vtype=''.join([x.xpath('string()').strip() for x in row.xpath('td') if x.xpath('string()').find(u"ПОПРАВКИ В ПОДАДЕНИТЕ ГЛАСОВЕ И НАМЕРЕНИЯ ЗА ГЛАСУВАНЕ")==-1])
                if u'Υπέρ' in vtype or u'ΥΠΕΡ' in vtype:
                    k="+"
                if u'Κατά' in vtype or u'ΚΑΤΑ' in vtype:
                    k="-"
                if u'Απoχές' in vtype or u'ΑΠOΧΕΣ' in vtype:
                    k="0"
                try:
                    voters=row.xpath('following-sibling::tr')[0].xpath('string()').strip()
                except IndexError:
                    voters=""
                skip=True

            if k not in ['0','+','-']: continue
            voters=[x.strip() for x in voters.split(',') if x.strip()]
            if not voters:
                continue
            vote[k]['correctional']=[]
            for name in voters:
                mep=None
                vote[k]['correctional'].append(name)
        res.append(vote)
    return res

if __name__ == "__main__":
    import platform
    if platform.machine() in ['i386', 'i686']:
        import psyco
        psyco.full()
    #scrape(sys.argv[1])
    print json.dumps(scrape(sys.argv[1]),indent=1, default=dateJSONhandler)

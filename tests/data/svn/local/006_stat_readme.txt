PROPFIND /svn/open/trunk/README
> Depth: 0
> Content-Type: text/xml; charset=utf-8
> Body: <?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:" xmlns:s="http://subversion.tigris.org/xmlns/dav/" xmlns:v="http://subversion.tigris.org/xmlns/svn/"><prop><resourcetype/><getcontentlength/><getlastmodified/><v:special/><s:md5-checksum/><s:sha1-checksum/></prop></propfind>

HTTP 207 Multi-Status
Date: Fri, 16 Oct 2026 16:12:10 GMT
Server: Apache/2.4.69 (Fedora Linux) SVN/1.14.5
Content-Length: 872
Content-Type: text/xml; charset="utf-8"

[body: 872 bytes]
<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:ns2="http://subversion.tigris.org/xmlns/dav/" xmlns:ns1="http://subversion.tigris.org/xmlns/svn/" xmlns:ns0="DAV:">
<D:response xmlns:lp1="DAV:" xmlns:lp2="http://subversion.tigris.org/xmlns/dav/" xmlns:g0="http://subversion.tigris.org/xmlns/svn/">
<D:href>/svn/open/trunk/README</D:href>
<D:propstat>
<D:prop>
<lp1:resourcetype/>
<lp1:getcontentlength>6</lp1:getcontentlength>
<lp1:getlastmodified>Fri, 16 Oct 2026 15:52:18 GMT</lp1:getlastmodified>
<lp2:md5-checksum>b1946ac92492d2347c6235b4d2611184</lp2:md5-checksum>
<lp2:sha1-checksum>f572d396fae9206628714fb2ce00f72e94f2258f</lp2:sha1-checksum>
</D:prop>
<D:status>HTTP/1.1 200 OK</D:status>
</D:propstat>
<D:propstat>
<D:prop>
<g0:special/>
</D:prop>
<D:status>HTTP/1.1 404 Not Found</D:status>
</D:propstat>
</D:response>
</D:multistatus>

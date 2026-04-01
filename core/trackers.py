"""
Massive public tracker list for maximum peer discovery.
These are the most reliable, high-uptime public trackers as of 2024-2025.
Sources: ngosang/trackerslist, newTrackon
"""

PUBLIC_TRACKERS = [
    # === Top-tier UDP trackers (fastest, most peers) ===
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://public.popcorn-tracker.org:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://opentor.net:6969/announce",
    "udp://opentor.org:2710/announce",
    
    # === High-capacity UDP trackers ===
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://9.rarbg.me:2730/announce",
    "udp://9.rarbg.me:2770/announce",
    "udp://9.rarbg.to:2720/announce",
    "udp://9.rarbg.to:2730/announce",
    "udp://9.rarbg.to:2770/announce",
    "udp://tracker.internetwarriors.net:1337/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.pirateparty.gr:6969/announce",
    
    # === Reliable UDP trackers ===
    "udp://tracker.cyberia.is:6969/announce",
    "udp://tracker.port443.xyz:6969/announce",
    "udp://tracker.zer0day.to:1337/announce",
    "udp://retracker.lanta-net.ru:2710/announce",
    "udp://tracker.sbsub.com:2710/announce",
    "udp://tracker.filemail.com:6969/announce",
    "udp://tracker.birkenwald.de:6969/announce",
    "udp://tracker.altrosky.nl:6969/announce",
    "udp://tracker.army:6969/announce",
    "udp://tracker.leech.ie:1337/announce",
    "udp://tracker.ds.is:6969/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://movies.zsw.ca:6969/announce",
    "udp://ipv4.tracker.harry.lu:80/announce",
    "udp://tracker.zemoj.com:6969/announce",
    "udp://tracker.0x.tf:6969/announce",
    "udp://retracker.sevstar.net:2710/announce",
    "udp://open.tracker.ink:6969/announce",
    "udp://tracker.srv00.com:6969/announce",
    "udp://tracker.theoks.net:6969/announce",
    "udp://tracker.dump.cl:6969/announce",
    "udp://tracker.filepit.to:6969/announce",
    "udp://tracker.swarm.pp.ua:6969/announce",
    "udp://retracker.nts.su:2710/announce",
    
    # === Additional worldwide UDP trackers ===
    "udp://bt1.archive.org:6969/announce",
    "udp://bt2.archive.org:6969/announce",
    "udp://tracker.jordan.im:6969/announce",
    "udp://tracker.lelux.fi:6969/announce",
    "udp://tracker.skyts.net:6969/announce",
    "udp://tracker.qu.ax:6969/announce",
    "udp://tracker.fnix.net:6969/announce",
    "udp://evan.im:6969/announce",
    "udp://martin-gebhardt.eu:25/announce",
    "udp://tracker.ducks.party:1984/announce",
    "udp://tracker.tryhackx.org:6969/announce",
    "udp://tracker.gmi.gd:6969/announce",
    "udp://tracker.dhitechnical.com:6969/announce",
    "udp://d40969.acod.regrucolo.ru:6969/announce",
    "udp://bandito.byterunner.io:6969/announce",
    "udp://tracker.dler.com:6969/announce",
    "udp://tracker.pmman.tech:6969/announce",
    "udp://tracker.myalfu.best:6969/announce",
    "udp://tracker.srv00.com:6969/announce",
    "udp://tracker.zhuqiy.com:6969/announce",
    "udp://torrentclub.online:54123/announce",
    "udp://open.demonoid.ch:6969/announce",
    "udp://public.demonoid.ch:6969/announce",
    "udp://tracker.plx.im:6969/announce",
    "udp://tracker.tvunderground.org.ru:3218/announce",
    "udp://rekcart.duckdns.org:15480/announce",
    "udp://tracker.playground.ru:6969/announce",
    "udp://tracker.skynetcloud.site:6969/announce",
    
    # === HTTP/HTTPS trackers (backup) ===
    "http://tracker.opentrackr.org:1337/announce",
    "https://tracker.tamersunion.org:443/announce",
    "http://tracker.gbitt.info:80/announce",
    "http://tracker.bt4g.com:2095/announce",
    "http://tracker.renfei.net:8080/announce",
    "https://shahidrazi.online:443/announce",
    "https://tracker.moeblog.cn:443/announce",
    "https://tracker.loligirl.cn:443/announce",
    "https://tracker.lilithraws.org:443/announce",
    "http://open.acgnxtracker.com:80/announce",
    "https://tracker.kuroy.me:443/announce",
    "http://tracker.mywaifu.best:6969/announce",
    "https://tracker.yemekyedim.com:443/announce",
    "https://tracker.leechshield.link:443/announce",
    "http://bt.okmp3.ru:2710/announce",
    "http://tracker.dler.com:6969/announce",
    "https://tracker.ghostchu-services.top:443/announce",
    "https://aboutbeautifulgallopinghorsesinthegreen.online:443/announce",
    "https://tracker.belmut.online:443/announce",
    "https://cny.fan:443/announce",
    "https://tracker.pmman.tech:443/announce",
    "http://www.torrent.eu.org:451/announce",
    "http://tracker.therarb.org:6969/announce",
    "http://open.tracker.ink:6969/announce",
]


def get_all_trackers(torrent_trackers: list) -> list:
    """
    Merge the torrent's own trackers with our public tracker list.
    Returns a deduplicated list with torrent's own trackers first.
    """
    seen = set()
    result = []
    
    # Torrent's own trackers first (higher priority)
    for url in torrent_trackers:
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    
    # Add public trackers that aren't already included
    for url in PUBLIC_TRACKERS:
        if url not in seen:
            seen.add(url)
            result.append(url)
    
    return result

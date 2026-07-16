# -*- coding: utf-8 -*-
"""
搜书吧论坛登入、发布空间动态脚本（修复版）
- 修复 SSL 证书验证问题
- 修复正则匹配无结果导致的 AttributeError
- 增加健壮性检查与调试日志
"""
import os
import re
import sys
import time
import logging
import xml.etree.ElementTree as ET
from copy import copy
from urllib.parse import urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

# 禁用不安全请求警告（因为使用了 verify=False）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)


def get_refresh_url(url: str):
    """
    从包含 meta refresh 的页面中提取重定向 URL
    """
    if not url:
        logger.error("get_refresh_url received empty url")
        return None
    try:
        # 忽略 SSL 验证，添加超时
        response = requests.get(url, verify=False, timeout=10)
        # 即使状态码不是 200，也尝试解析（有些页面会返回 403 但包含 meta 刷新）
        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', {'http-equiv': 'refresh'})
        if meta_tags:
            content = meta_tags[0].get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"Redirecting to: {redirect_url}")
                return redirect_url
            else:
                logger.warning("Meta refresh tag found but no url= in content")
        else:
            logger.warning("No meta refresh tag found in response")
            logger.debug(f"Page snippet: {response.text[:300]}")
        return None
    except Exception as e:
        logger.exception(f'Unexpected error while getting refresh URL: {e}')
        return None


def get_url(url: str):
    """
    从页面中提取指向“搜书吧”的链接
    """
    if url is None:
        logger.error("get_url received None, cannot proceed.")
        return None
    try:
        resp = requests.get(url, verify=False, timeout=10)
        soup = BeautifulSoup(resp.content, 'html.parser')
        links = soup.find_all('a', href=True)
        for link in links:
            if link.text.strip() == "搜书吧":
                return link['href']
        logger.warning("No link with text '搜书吧' found.")
        return None
    except Exception as e:
        logger.exception(f'Error in get_url: {e}')
        return None


class SouShuBaClient:
    """
    搜书吧客户端，处理登录、发动态、查询积分
    """

    def __init__(self, hostname: str, username: str, password: str,
                 questionid: str = '0', answer: str = None,
                 proxies: dict | None = None):
        self.session = requests.Session()
        self.hostname = hostname
        self.username = username
        self.password = password
        self.questionid = questionid
        self.answer = answer
        self._common_headers = {
            "Host": f"{hostname}",
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        self.proxies = proxies

    def login_form_hash(self):
        """获取登录所需的 loginhash 和 formhash（带重试）"""
        last_exception = None
        for attempt in range(3):
            try:
                resp = self.session.get(
                    f'https://{self.hostname}/member.php?mod=logging&action=login',
                    verify=False, timeout=10
                )
                resp.encoding = 'utf-8'
                rst = resp.text
    
                # 安全提取 loginhash
                match = re.search(r'<div id="main_messaqge_(.+?)">', rst)
                if not match:
                    raise ValueError("loginhash not found in page")
                loginhash = match.group(1)
    
                # 安全提取 formhash
                match = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', rst)
                if not match:
                    raise ValueError("formhash not found in page")
                formhash = match.group(1)
    
                return loginhash, formhash
    
            except Exception as e:
                last_exception = e
                logger.warning(f"login_form_hash attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(10)
                else:
                    logger.error("login_form_hash failed after 3 attempts")
                    raise last_exception

    def login(self):
        """使用用户名密码登录"""
        loginhash, formhash = self.login_form_hash()
        login_url = f'https://{self.hostname}/member.php?mod=logging&action=login&loginsubmit=yes' \
                    f'&handlekey=register&loginhash={loginhash}&inajax=1'

        headers = copy(self._common_headers)
        headers["origin"] = f'https://{self.hostname}'
        headers["referer"] = f'https://{self.hostname}/'
        payload = {
            'formhash': formhash,
            'referer': f'https://{self.hostname}/',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer or ''
        }

        resp = self.session.post(login_url, proxies=self.proxies, data=payload,
                                 headers=headers, verify=False, timeout=10)
        if resp.status_code == 200 and '欢迎您回来' in resp.text or 'succeed' in resp.text.lower():
            logger.info(f'Welcome {self.username}!')
        else:
            logger.error(f"Login failed, status: {resp.status_code}, response snippet: {resp.text[:200]}")
            raise ValueError('Verify Failed! Check your username and password!')

    def credit(self):
        """获取当前积分余额"""
        credit_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu"
        try:
            credit_rst = self.session.get(credit_url, verify=False, timeout=10).text
            root = ET.fromstring(credit_rst)
            cdata_content = root.text
            cdata_soup = BeautifulSoup(cdata_content, features="lxml")
            hcredit_2 = cdata_soup.find("span", id="hcredit_2")
            if hcredit_2:
                return hcredit_2.string
            else:
                logger.warning("Could not find credit span, full cdata: %s", cdata_content[:200])
                return "0"
        except Exception as e:
            logger.exception(f"Failed to get credit: {e}")
            return "0"

    def space_form_hash(self):
        """获取空间动态页面的 formhash"""
        try:
            resp = self.session.get(
                f'https://{self.hostname}/home.php?mod=spacecp&ac=credit',
                verify=False, timeout=10
            )
            resp.encoding = 'utf-8'
            rst = resp.text
        except Exception as e:
            logger.exception("Failed to fetch space page")
            raise

        match = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', rst)
        if not match:
            logger.error("Could not find formhash in space page")
            logger.debug(f"Response snippet: {rst[:500]}")
            raise ValueError("Space page structure changed or blocked (formhash missing).")
        return match.group(1)

    def space(self):
        """发布 5 条空间动态，间隔 120 秒（带掉线自动重连）"""
        space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"
    
        headers = copy(self._common_headers)
        headers["origin"] = f'https://{self.hostname}'
        headers["referer"] = f'https://{self.hostname}/home.php'
    
        for x in range(5):
            formhash = None
            for retry in range(3):
                try:
                    formhash = self.space_form_hash()
                    break
                except Exception as e:
                    logger.warning(f"Get formhash attempt {retry+1} failed: {e}")
                    if retry < 2:
                        # 尝试重新登录，解决因掉线导致的 formhash 缺失
                        try:
                            logger.info("Trying to re-login to recover session...")
                            self.login()
                            time.sleep(5)   # 给服务器一点反应时间
                        except Exception as le:
                            logger.warning(f"Re-login attempt failed: {le}")
                        time.sleep(10)      # 等待后继续重试
                    else:
                        logger.error(f"Failed to get formhash after 3 attempts, skipping post {x+1}")
                        formhash = None
                        break
    
            if formhash is None:
                continue  # 跳过本次发布，尝试下一次
    
            payload = {
                "message": f"开心赚银币 {x + 1} 次",
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            try:
                resp = self.session.post(space_url, proxies=self.proxies, data=payload,
                                         headers=headers, verify=False, timeout=10)
                if "操作成功" in resp.text or "成功" in resp.text:
                    logger.info(f'{self.username} post {x + 1}nd successfully!')
                    if x < 4:
                        time.sleep(120)
                else:
                    logger.warning(f'{self.username} post {x + 1}nd failed! Response: {resp.text[:100]}')
            except Exception as e:
                logger.exception(f"Post {x+1} exception: {e}")
            
if __name__ == '__main__':
    try:
        # 从环境变量获取配置
        base_host = os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
        username = os.environ.get('SOUSHUBA_USERNAME', 'USERNAME')
        password = os.environ.get('SOUSHUBA_PASSWORD', 'PASSWORD')

        logger.info(f"Starting with base host: {base_host}")

        # 第一步：获取初始重定向 URL
        redirect_url = get_refresh_url('http://' + base_host)
        if not redirect_url:
            raise ValueError("Failed to get first redirect URL")

        time.sleep(2)

        # 第二步：从第一次重定向结果中获取下一个重定向 URL
        redirect_url2 = get_refresh_url(redirect_url)
        if not redirect_url2:
            raise ValueError("Failed to get second redirect URL")

        # 第三步：从页面中提取真正的论坛入口链接
        target_url = get_url(redirect_url2)
        if not target_url:
            raise ValueError("Failed to extract target URL from redirect")

        logger.info(f'Target URL: {target_url}')

        # 解析主机名并创建客户端
        parsed_host = urlparse(target_url).hostname
        if not parsed_host:
            raise ValueError("Could not parse hostname from target URL")

        client = SouShuBaClient(parsed_host, username, password)

        # 执行登录、发动态、查询积分
        client.login()
        client.space()
        credit = client.credit()
        logger.info(f'{client.username} have {credit} coins!')

    except Exception as e:
        logger.exception("Script terminated with error")
        sys.exit(1)

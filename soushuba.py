# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登入和发布空间动态（SSL验证修复版）
"""
import os
import re
import sys
from copy import copy

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import time
import logging
import urllib3

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

ch = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

def safe_request(url, max_retries=3):
    """安全请求函数，自动处理SSL验证问题"""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=max_retries)
    session.mount('https://', adapter)
    
    try:
        # 首先尝试严格模式
        return session.get(url, verify=True, timeout=10)
    except requests.exceptions.SSLError:
        logger.warning(f"SSL验证失败，尝试不验证证书访问 {url}")
        return session.get(url, verify=False, timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"请求失败: {str(e)}")
        return None

def get_refresh_url(url: str):
    try:
        response = safe_request(url)
        if not response or response.status_code != 403:
            if response:
                response.raise_for_status()
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', {'http-equiv': 'refresh'})

        if meta_tags:
            content = meta_tags[0].get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"Redirecting to: {redirect_url}")
                return redirect_url
        else:
            logger.info("No meta refresh tag found.")
            return None
    except Exception as e:
        logger.error(f'An unexpected error occurred: {e}')
        return None

def get_url(url: str):
    resp = safe_request(url)
    if not resp:
        return None
    
    soup = BeautifulSoup(resp.content, 'html.parser')
    links = soup.find_all('a', href=True)
    for link in links:
        if link.text == "搜书吧":
            return link['href']
    return None

class SouShuBaClient:

    def __init__(self, hostname: str, username: str, password: str, questionid: str = '0', answer: str = None,
                 proxies: dict | None = None):
        self.session = requests.Session()
        # 配置Session全局参数
        self.session.verify = False  # 关闭全局验证
        self.session.headers.update({
            "Host": f"{hostname}",
            "Connection": "keep-alive",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,cn;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        self.hostname = hostname
        self.username = username
        self.password = password
        self.questionid = questionid
        self.answer = answer
        self.proxies = proxies

    def login_form_hash(self):
        try:
            # 显式设置不验证SSL
            rst = self.session.get(
                f'https://{self.hostname}/member.php?mod=logging&action=login',
                verify=False
            ).text
            loginhash = re.search(r'<div id="main_messaqge_(.+?)">', rst).group(1)
            formhash = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', rst).group(1)
            return loginhash, formhash
        except AttributeError:
            raise ValueError("无法解析登录表单，请检查网站结构是否变化")

    def login(self):
        """Login with username and password"""
        loginhash, formhash = self.login_form_hash()
        login_url = f'https://{self.hostname}/member.php?mod=logging&action=login&loginsubmit=yes' \
                    f'&handlekey=register&loginhash={loginhash}&inajax=1'

        headers = {
            "origin": f'https://{self.hostname}',
            "referer": f'https://{self.hostname}/'
        }
        
        payload = {
            'formhash': formhash,
            'referer': f'https://{self.hostname}/',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer
        }

        resp = self.session.post(
            login_url,
            proxies=self.proxies,
            data=payload,
            headers=headers,
            verify=False  # 确保本次请求不验证
        )
        
        if resp.status_code == 200:
            logger.info(f'Welcome {self.username}!')
            # 检查实际登录是否成功
            if "欢迎您回来" not in resp.text:
                raise ValueError('登录验证失败，请检查凭据或网站状态')
        else:
            resp.raise_for_status()

    def credit(self):
        credit_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu"
        try:
            credit_rst = self.session.get(credit_url, verify=False).text
            root = ET.fromstring(credit_rst)
            cdata_content = root.text
            cdata_soup = BeautifulSoup(cdata_content, features="lxml")
            return cdata_soup.find("span", id="hcredit_2").string
        except Exception as e:
            logger.error(f"获取积分失败: {str(e)}")
            return "N/A"

    def space_form_hash(self):
        rst = self.session.get(f'https://{self.hostname}/home.php', verify=False).text
        try:
            return re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', rst).group(1)
        except AttributeError:
            raise ValueError("无法解析formhash，请检查登录状态")

    def space(self):
        try:
            formhash = self.space_form_hash()
            space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"
            
            headers = {
                "origin": f'https://{self.hostname}',
                "referer": f'https://{self.hostname}/home.php'
            }

            for x in range(5):
                payload = {
                    "message": f"开心赚银币 {x + 1} 次".encode("GBK"),
                    "addsubmit": "true",
                    "spacenote": "true",
                    "referer": "home.php",
                    "formhash": formhash
                }
                resp = self.session.post(
                    space_url,
                    proxies=self.proxies,
                    data=payload,
                    headers=headers,
                    verify=False
                )
                if re.search("操作成功", resp.text):
                    logger.info(f'{self.username} 第 {x + 1} 次发布成功!')
                    time.sleep(120)
                else:
                    logger.warning(f'{self.username} 第 {x + 1} 次发布失败!')
                    break  # 失败时终止循环
        except Exception as e:
            logger.error(f"发布动态时发生错误: {str(e)}")
            raise

if __name__ == '__main__':
    try:
        # 初始化时禁用SSL验证
        requests.packages.urllib3.disable_warnings()
        
        redirect_url = get_refresh_url('http://' + os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2025.com'))
        time.sleep(2)
        redirect_url2 = get_refresh_url(redirect_url)
        url = get_url(redirect_url2)
            
        logger.info(f'解析成功: {url}')
        client = SouShuBaClient(
            urlparse(url).hostname,
            os.environ.get('SOUSHUBA_USERNAME', "libesse"),
            os.environ.get('SOUSHUBA_PASSWORD', "yF9pnSBLH3wpnLd")
        )
        client.login()
        client.space()
        logger.info(f'{client.username} 当前积分: {client.credit()}')
    except Exception as e:
        logger.error(f"主程序错误: {str(e)}")
        sys.exit(1)

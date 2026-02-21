# -*- coding: utf-8 -*-
"""
实现搜书吧论坛登入和发布空间动态
修复 SSL 证书验证失败问题，并增加健壮性检查
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
    try:
        # 忽略 SSL 验证，添加超时
        response = requests.get(url, verify=False, timeout=10)
        if response.status_code != 403:
            response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        meta_tags = soup.find_all('meta', {'http-equiv': 'refresh'})

        if meta_tags:
            content = meta_tags[0].get('content', '')
            if 'url=' in content:
                redirect_url = content.split('url=')[1].strip()
                logger.info(f"Redirecting to: {redirect_url}")
                return redirect_url
        else:
            logger.error("No meta refresh tag found.")
            return None
    except Exception as e:
        logger.exception(f'An unexpected error occurred while getting refresh URL: {e}')
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
            if link.text == "搜书吧":
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

    def __init__(self, hostname: str, username: str, password: str, questionid: str = '0', answer: str = None,
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
        """获取登录所需的 loginhash 和 formhash"""
        rst = self.session.get(f'https://{self.hostname}/member.php?mod=logging&action=login', verify=False).text
        loginhash = re.search(r'<div id="main_messaqge_(.+?)">', rst).group(1)
        formhash = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', rst).group(1)
        return loginhash, formhash

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
            'answer': self.answer
        }

        resp = self.session.post(login_url, proxies=self.proxies, data=payload, headers=headers, verify=False)
        if resp.status_code == 200:
            logger.info(f'Welcome {self.username}!')
        else:
            raise ValueError('Verify Failed! Check your username and password!')

    def credit(self):
        """获取当前积分余额"""
        credit_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=credit&showcredit=1&inajax=1&ajaxtarget=extcreditmenu_menu"
        credit_rst = self.session.get(credit_url, verify=False).text

        # 解析 XML，提取 CDATA
        root = ET.fromstring(credit_rst)
        cdata_content = root.text

        # 使用 BeautifulSoup 解析 CDATA 内容
        cdata_soup = BeautifulSoup(cdata_content, features="lxml")
        hcredit_2 = cdata_soup.find("span", id="hcredit_2").string
        return hcredit_2

    def space_form_hash(self):
        """获取空间动态页面的 formhash"""
        rst = self.session.get(f'https://{self.hostname}/home.php?mod=spacecp&ac=credit', verify=False).text
        formhash = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', rst).group(1)
        return formhash

    def space(self):
        """发布 5 条空间动态，间隔 120 秒"""
        formhash = self.space_form_hash()
        space_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=doing&handlekey=doing&inajax=1"

        headers = copy(self._common_headers)
        headers["origin"] = f'https://{self.hostname}'
        headers["referer"] = f'https://{self.hostname}/home.php'

        for x in range(5):
            payload = {
                "message": f"开心赚银币 {x + 1} 次".encode("GBK"),
                "addsubmit": "true",
                "spacenote": "true",
                "referer": "home.php",
                "formhash": formhash
            }
            resp = self.session.post(space_url, proxies=self.proxies, data=payload, headers=headers, verify=False)
            if re.search("操作成功", resp.text):
                logger.info(f'{self.username} post {x + 1}nd successfully!')
                if x < 4:  # 最后一次不需要等待
                    time.sleep(120)
            else:
                logger.warning(f'{self.username} post {x + 1}nd failed!')


if __name__ == '__main__':
    try:
        # 从环境变量获取配置
        base_host = os.environ.get('SOUSHUBA_HOSTNAME', 'www.soushu2035.com')
        username = os.environ.get('SOUSHUBA_USERNAME', 'USERNAME')
        password = os.environ.get('SOUSHUBA_PASSWORD', 'PASSWORD')

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
        client = SouShuBaClient(parsed_host, username, password)

        # 执行登录、发动态、查询积分
        client.login()
        client.space()
        credit = client.credit()
        logger.info(f'{client.username} have {credit} coins!')

    except Exception as e:
        logger.error(e)
        sys.exit(1)

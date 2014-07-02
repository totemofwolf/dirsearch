# -*- coding: utf-8 -*-
import threading
import logging
import signal
from Queue import Queue
from config import *
from lib.connection import *
from FuzzerDictionary import *
from NotFoundTester import *
from ReportManager import *
from lib.reports import *
import threading
import time


class Fuzzer(object):

    def __init__(self, requester, dictionary, output, threads=1, recursive=True, reportManager=None, blacklists={},
                 excludeInternalServerError=False):
        self.requester = requester
        self.dictionary = dictionary
        self.blacklists = blacklists
        self.basePath = self.requester.basePath
        self.output = output
        self.excludeInternalServerError = excludeInternalServerError
        self.threads = []
        self.threadsCount = threads
        self.running = False
        self.directories = Queue()
        self.testers = {}
        self.recursive = recursive
        self.currentDirectory = ''
        self.indexMutex = threading.Lock()
        self.index = 0
        # Setting up testers
        self.testersSetup()
        # Setting up threads
        self.threadsSetup()
        self.reportManager = (ReportManager() if reportManager is None else reportManager)

    def testersSetup(self):
        if len(self.testers) != 0:
            self.testers = {}
        self.testers['/'] = NotFoundTester(self.requester, '{0}/'.format(NOT_FOUND_PATH))
        for extension in self.dictionary.extensions:
            self.testers[extension] = NotFoundTester(self.requester, '{0}.{1}'.format(NOT_FOUND_PATH, extension))

    def threadsSetup(self):
        if len(self.threads) != 0:
            self.threads = []
        for thread in range(self.threadsCount):
            newThread = threading.Thread(target=self.thread_proc)
            newThread.daemon = True
            self.threads.append(newThread)

    def getTester(self, path):
        for extension in self.testers.keys():
            if path.endswith(extension):
                return self.testers[extension]
        # By default, returns folder tester
        return self.testers['/']

    def start(self):
        self.index = 0
        self.dictionary.reset()
        self.runningThreadsCount = len(self.threads)
        self.stoppedByUser = False
        self.running = True
        self.finishedCondition = threading.Condition()
        self.finishedThreadCondition = threading.Condition()
        for thread in self.threads:
            thread.start()

    def wait(self):
        # Sleep makes the OS to switch to another thread
        self.finishedCondition.acquire()
        while self.running:
            self.finishedCondition.wait(1)
        self.finishedCondition.release()
        for thread in self.threads:
            thread.join()
        while not self.directories.empty():
            self.currentDirectory = self.directories.get()
            self.output.printWarning('\nSwitching to founded directory: {0}'.format(self.currentDirectory))
            self.requester.basePath = '{0}{1}'.format(self.basePath, self.currentDirectory)
            self.output.basePath = '{0}{1}'.format(self.basePath, self.currentDirectory)
            self.testersSetup()
            self.threadsSetup()
            self.start()
            self.finishedCondition.acquire()
            while self.running:
                self.finishedCondition.wait(1)
            self.finishedCondition.release()
            for thread in self.threads:
                thread.join()
        self.reportManager.save()
        self.reportManager.close()
        return

    def testPath(self, path):
        response = self.requester.request(path)
        result = 0
        if self.getTester(path).test(response):
            result = (0 if response.status == 404 else response.status)
        return result, response

    def addDirectory(self, path):
        if self.recursive == False:
            return False
        if path.endswith('/'):
            if self.currentDirectory == '':
                self.directories.put(path)
            else:
                self.directories.put('{0}{1}'.format(self.currentDirectory, path))
            return True
        else:
            return False

    def finishThreads(self):
        self.finishedCondition.acquire()
        self.running = False
        self.finishedCondition.notify()
        self.finishedCondition.release()

    def thread_proc(self):
        try:
            path = self.dictionary.next()
            while path is not None:
                try:
                    status, response = self.testPath(path)
                    if status is not 0:
                        if self.blacklists.get(status) is None or path not in self.blacklists.get(status):
                            self.output.printStatusReport(path, response)
                            self.addDirectory(path)
                            self.reportManager.addPath(status, self.currentDirectory + path)
                    self.indexMutex.acquire()
                    self.index += 1
                    self.output.printLastPathEntry(path, self.index, len(self.dictionary))
                    self.indexMutex.release()
                    path = self.dictionary.next()
                    if not self.running:
                        break
                    if path is None:
                        self.running = False
                        self.finishThreads()
                except RequestException, e:
                    self.output.printError('Unexpected error:\n{0}'.format(e.args[0]['message']))
                    continue
        except KeyboardInterrupt, SystemExit:
            self.running = False
            self.finishThreads()